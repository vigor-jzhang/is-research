# Documentation index

This directory is organized by the task you are trying to complete. Start with the overview below, then use the guide that matches your work.

## Essentials

| Need | Read |
| --- | --- |
| Understand the system design and plugin boundaries | [Architecture](concepts/architecture.md) |
| Configure a local or provider-backed run | [Configuration](getting-started/configuration.md) |
| Find an operator command | [CLI reference](getting-started/cli.md) |
| Understand artifacts, provenance, and persistence | [Research domain](concepts/research-domain.md) |
| Build an external plugin | [Plugin authoring](concepts/plugin-authoring.md) |

## Research journeys

Choose the guide that matches the outcome you need. You can enter at any point when you already have the required input artifact.

| Goal | Start with | Produces | Guides |
| --- | --- | --- | --- |
| Build a trustworthy literature base | Sources and search strategy | A screened, traceable literature set | [Literature sources](workflows/literature/literature-sources.md), [Search strategy](workflows/literature/search-strategy.md), [Screening](workflows/literature/screening.md) |
| Acquire and interpret research evidence | Included literature set | Full-text corpus and page-grounded evidence | [Documents](workflows/literature/documents.md), [Evidence extraction](workflows/literature/evidence.md), [Synthesis](workflows/literature/synthesis.md) |
| Identify a research opportunity | Literature synthesis | Evidence-grounded research gaps | [Gap analysis](workflows/literature/gaps.md) |
| Develop an analytical explanation | Selected research gap | Mechanism, model, verified equilibrium, and propositions | [Mechanisms](workflows/theory/mechanisms.md), [Model specification](workflows/theory/models.md), [Equilibrium](workflows/theory/equilibrium.md), [Propositions](workflows/theory/propositions.md) |
| Test implications and assemble findings | Verified analytical results | Numerical results and a research-results package | [Numerical analysis](workflows/theory/numerical.md), [Results](workflows/outputs/results.md) |
| Prepare a submission | Research-results package | Draft, formatted manuscript, and submission package | [Manuscript](workflows/outputs/manuscript.md), [Publication](workflows/outputs/publication.md) |
| Reassess novelty and evidence | Submission package or revised manuscript | Novelty report, gate, and revalidation record | [Novelty validation](workflows/outputs/novelty.md) |

## Evaluation and model operations

| Need | Read |
| --- | --- |
| Run or interpret offline benchmarks | [Evaluation harness](operations/evaluation.md) |
| Compare models or inspect routing policy | [Evaluation harness](operations/evaluation.md#model-tournaments--role-leaderboards), [Model routing status](operations/model-routing-status.md) |
| Run live-model qualification and readiness checks | [Evaluation harness](operations/evaluation.md#live-quality-validation--production-routing-readiness) |

## Reading conventions

- Guides explain purpose, artifacts, safeguards, configuration, CLI usage, and tests for one capability.
- The [CLI reference](getting-started/cli.md) is exhaustive; guide pages contain only the commands most relevant to that capability.
- Architecture and research-domain pages define cross-cutting rules. They take precedence where a capability guide omits shared behavior.
- Live tests and external-provider commands are opt-in. Check credentials, cost, and network requirements before running them.
