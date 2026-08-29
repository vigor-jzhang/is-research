# Documentation index

This directory is organized by the task you are trying to complete. Start with the overview below, then use the phase guide that matches your work.

## Essentials

| Need | Read |
| --- | --- |
| Understand the system design and plugin boundaries | [Architecture](architecture.md) |
| Configure a local or provider-backed run | [Configuration](configuration.md) |
| Find an operator command | [CLI reference](cli.md) |
| Understand artifacts, provenance, and persistence | [Research domain](research-domain.md) |
| Build an external plugin | [Plugin authoring](plugin-authoring.md) |

## Research workflow

Follow these guides in order when executing the end-to-end research workflow.

| Phase | Outcome | Guide |
| --- | --- | --- |
| 2A–2C | Sources, identity resolution, and search strategy | [Literature sources](literature-sources.md), [Search strategy](search-strategy.md) |
| 2D | Protocol-driven screening | [Screening](screening.md) |
| 2E | Locate, acquire, and extract documents | [Documents](documents.md) |
| 2F | Page-grounded evidence | [Evidence extraction](evidence.md) |
| 2G | Cross-paper synthesis | [Synthesis](synthesis.md) |
| 2H | Evidence-grounded research gaps | [Gap analysis](gaps.md) |
| 3A | Mechanism development | [Mechanisms](mechanisms.md) |
| 3B | Formal analytical model | [Model specification](models.md) |
| 3C | Symbolic equilibrium derivation | [Equilibrium](equilibrium.md) |
| 3D | Comparative statics and propositions | [Propositions](propositions.md) |
| 3E | Numerical experiments and welfare analysis | [Numerical analysis](numerical.md) |
| 4A | Results assembly | [Results](results.md) |
| 4B | Manuscript drafting | [Manuscript](manuscript.md) |
| 4C | Submission-package formatting and export | [Publication](publication.md) |
| 5A–5D | Novelty validation and revalidation | [Novelty validation](novelty.md) |

## Evaluation and model operations

| Need | Read |
| --- | --- |
| Run or interpret offline benchmarks | [Evaluation harness](evaluation.md) |
| Compare models or inspect routing policy | [Evaluation harness](evaluation.md#model-tournaments--role-leaderboards-phase-7b), [Model routing status](model-routing-status.md) |
| Run live-model qualification and readiness checks | [Evaluation harness](evaluation.md#live-quality-validation--production-routing-readiness-phase-7d0) |

## Reading conventions

- Phase guides explain purpose, artifacts, safeguards, configuration, CLI usage, and tests for one capability.
- The [CLI reference](cli.md) is exhaustive; phase guides contain only the commands most relevant to that phase.
- Architecture and research-domain pages define cross-cutting rules. They take precedence where a phase guide omits shared behavior.
- Live tests and external-provider commands are opt-in. Check credentials, cost, and network requirements before running them.
