# Documentation index

This directory is organized by the task you are trying to complete. Start with the overview below, then use the phase guide that matches your work.

## Essentials

| Need | Read |
| --- | --- |
| Understand the system design and plugin boundaries | [Architecture](concepts/architecture.md) |
| Configure a local or provider-backed run | [Configuration](getting-started/configuration.md) |
| Find an operator command | [CLI reference](getting-started/cli.md) |
| Understand artifacts, provenance, and persistence | [Research domain](concepts/research-domain.md) |
| Build an external plugin | [Plugin authoring](concepts/plugin-authoring.md) |

## Research workflow

Follow these guides in order when executing the end-to-end research workflow.

| Phase | Outcome | Guide |
| --- | --- | --- |
| 2A–2C | Sources, identity resolution, and search strategy | [Literature sources](workflows/literature/literature-sources.md), [Search strategy](workflows/literature/search-strategy.md) |
| 2D | Protocol-driven screening | [Screening](workflows/literature/screening.md) |
| 2E | Locate, acquire, and extract documents | [Documents](workflows/literature/documents.md) |
| 2F | Page-grounded evidence | [Evidence extraction](workflows/literature/evidence.md) |
| 2G | Cross-paper synthesis | [Synthesis](workflows/literature/synthesis.md) |
| 2H | Evidence-grounded research gaps | [Gap analysis](workflows/literature/gaps.md) |
| 3A | Mechanism development | [Mechanisms](workflows/theory/mechanisms.md) |
| 3B | Formal analytical model | [Model specification](workflows/theory/models.md) |
| 3C | Symbolic equilibrium derivation | [Equilibrium](workflows/theory/equilibrium.md) |
| 3D | Comparative statics and propositions | [Propositions](workflows/theory/propositions.md) |
| 3E | Numerical experiments and welfare analysis | [Numerical analysis](workflows/theory/numerical.md) |
| 4A | Results assembly | [Results](workflows/outputs/results.md) |
| 4B | Manuscript drafting | [Manuscript](workflows/outputs/manuscript.md) |
| 4C | Submission-package formatting and export | [Publication](workflows/outputs/publication.md) |
| 5A–5D | Novelty validation and revalidation | [Novelty validation](workflows/outputs/novelty.md) |

## Evaluation and model operations

| Need | Read |
| --- | --- |
| Run or interpret offline benchmarks | [Evaluation harness](operations/evaluation.md) |
| Compare models or inspect routing policy | [Evaluation harness](operations/evaluation.md#model-tournaments--role-leaderboards-phase-7b), [Model routing status](operations/model-routing-status.md) |
| Run live-model qualification and readiness checks | [Evaluation harness](operations/evaluation.md#live-quality-validation--production-routing-readiness-phase-7d0) |

## Reading conventions

- Phase guides explain purpose, artifacts, safeguards, configuration, CLI usage, and tests for one capability.
- The [CLI reference](getting-started/cli.md) is exhaustive; phase guides contain only the commands most relevant to that phase.
- Architecture and research-domain pages define cross-cutting rules. They take precedence where a phase guide omits shared behavior.
- Live tests and external-provider commands are opt-in. Check credentials, cost, and network requirements before running them.
