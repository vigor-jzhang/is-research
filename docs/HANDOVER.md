# Handover — finishing the code-review remediation

**Repository:** `/home/vigor/agentwork/is-research` (`research-harness`)
**Handover date:** 2026-09-08
**HEAD at handover:** `4d03cfe` — *Fix declared symbols and optional-dependency cycles: L7, L23*
**Branch:** `main`, remote `origin` (`git@github.com:vigor-jzhang/is-research.git`), fully pushed

---

## 1. What this work is

A full-repository code review was performed and written up in
[`docs/CODE_REVIEW_2026-08-29.md`](CODE_REVIEW_2026-08-29.md). It found **158 findings**:
8 Critical, 25 High, 86 Medium, 39 Low.

The dominant defect pattern in this codebase is **fail-open** — swallowed exceptions,
checks that cannot fire, and `None`/absent values scored as successes. In a research
harness (which is a measurement instrument) this silently manufactures scientific
claims, so the review's standard is: *when it fails it must fail loudly.*

Since the review there have been **34 fix rounds**. Critical and High are fully closed.
Read §1 (executive summary) and §6 (what the codebase gets right) of the report first —
§6 in particular explains the architecture you must not break.

### State at handover

| Tier | Total | Closed | Open |
|---|---|---|---|
| Critical | 8 | 8 | 0 |
| High | 25 | 25 | 0 |
| Medium | 86 | 81 | **5** |
| Low | 39 | 30 | **9** |
| **Total** | **158** | **144** | **13** |

**⚠ The Medium number is not trustworthy — reconcile it first.** The §9.0 tally row says
*81 closed / 5 open*, but the closed-ID enumeration directly beneath it
(`M1-M16, M17-M25, M26-M47, M49-M61, M63-M65, M66-M77, M79-M86`) sums to **83 closed / 3
open**: M48 (on M48c), M62, M78. Re-derive the count by finding ID before trusting either
number, and fix whichever is wrong.

Related: **many finding bodies in §4/§5 were never marked** even though the work was done
(e.g. M61 was fixed in round 29, M77/M81/M82/M86 in round 14, L20 in round 18 — none of
their bodies say so). Only later rounds reliably added `**Fixed (round N).**` markers. So
**do not infer "open" from the absence of a marker**; cross-check the round notes and the
§9.0 enumeration. M59 is the cautionary example: it was already fixed in `49c870e` and
sat in the report as open for 20 rounds.

---

## 2. How a round works

One round = pick a small coherent set of findings, fix them, verify, update the report.
Rounds 27–34 each took roughly 3–6 hours of agent time.

### The verification loop (order matters)

```bash
# 1. ALWAYS first — stale .pyc has masked reverted code more than once
find . -path ./.venv -prune -o -name "__pycache__" -type d -print0 | xargs -0 rm -rf

# 2. lint
.venv/bin/ruff check src tests          # must be "All checks passed!"

# 3. types (must be exactly 0 errors — the project is strict)
.venv/bin/pyright                       # "0 errors, 0 warnings, 0 informations"

# 4. full suite (~7 minutes)
.venv/bin/python -m pytest tests/unit tests/integration -q -p no:cacheprovider
```

Current baseline: **1393 passed**. A round is green only when all three are clean and the
suite has zero failures.

### Every new test must be proven to fail before the fix

```bash
git stash push -- src/                  # revert source, keep tests
find . -path ./.venv -prune -o -name "__pycache__" -type d -print0 | xargs -0 rm -rf
.venv/bin/python -m pytest tests/unit/test_<new>.py -q -p no:cacheprovider   # expect failures
git stash pop
find . -path ./.venv -prune -o -name "__pycache__" -type d -print0 | xargs -0 rm -rf
.venv/bin/python -m pytest tests/unit/test_<new>.py -q -p no:cacheprovider   # expect pass
```

**`git stash push -- src/` does not stash untracked files.** If a fix adds a new *source*
module, a test for it will pass against the reverted tree because the module is still
there. This actually happened (M31 / `research/timeutil.py`) and made a test look like a
regression test when it proved nothing.

Doing this properly catches tests that prove nothing. In round 29 two tests passed both
ways on the first attempt and had to be rewritten:
- the `extra: forbid` test asserted on construction, but models with required fields raise
  for the *wrong* reason (missing field) even when extra fields are allowed;
- the dotenv boundary test had the project's own `.env` shielding the defect.

### Do not commit unless asked

The standing instruction is to finish and report, then wait. The user says "commit" or
"commit and push". When they do: one commit per round, long message explaining *why*,
including the pre-fix test counts and the new suite total. See `git log` for the style.

### Report maintenance (do this every round)

In `docs/CODE_REVIEW_2026-08-29.md`:
1. Mark each finding body in §4/§5: `**Fixed (round N).**` or `**Partially fixed (round N).**`
2. Update the "Fix progress" table near the top (§1.1).
3. Update the §9.0 tally row **and** the closed-ID prose so they agree.
4. Add a `### Round N notes (...)` section immediately before the previous round's notes.

Round notes are the most valuable part of the report — they record what the fix was, what
was discovered while doing it, and what was deliberately *not* done and why. Write them
for the next person.

---

## 3. Remaining work

### Medium — 3 by enumeration (5 by the tally row): reconciling is task zero

| ID | What's left | Why it's not just code |
|---|---|---|
| **M48** | **M48c only** — a global concurrency cap | Deferred deliberately. There is **no concurrency anywhere in `src/`** (no `gather`, no `Semaphore`, no `create_task`). A semaphore added now would be inert — a control that reads as working and does nothing. Add it when concurrency is added. M48a (rate limiting) and M48b (locked pinned-address map) are done. |
| **M62** | Wildcard session subscriber blocks on a file append per event | Needs a product decision: "every event is on disk when the run returns" and "persistence off the critical path" cannot both hold without an explicit flush point callers must honour. A queue + background writer was implemented and reverted — it broke `test_e2e_mocked_run`. |
| **M78** | Live suite can't gate anything | Needs `OPENROUTER_API_KEY`. Not fixable as a regression gate. |

### Low — 9 open

| ID | Item | Note |
|---|---|---|
| **L33** | 3 of 6 nits remain | `runtime inspect` prints a "Services" heading over `metadata.provides`; 13 `__import__()` string hacks; a dead `or` branch that can raise `ValidationError`. All mechanical. |
| **L26** | `policies.py` frozen dataclass with mutable `list`/`dict` fields; `readiness.py` shares mutable criteria instances | Both small. |
| **L37** | Empty `plugins` list yields a runtime that silently does nothing | Careful: many tests construct configs with `plugins: []`, so a hard validator would be a breaking change — warn or reject at `build_runtime` instead. |
| **L5** | `evaluator_pipeline_integrity` — `evidence_artifact_ids` degenerates to "all produced artifacts" | Dilutes the evidence graph. |
| **L10** | `results_assembler` — on retry, findings are persisted twice, orphaning the first batch | **Examined and set aside.** The store is immutable so a partial batch cannot be rolled back; fixing it properly means restructuring `assemble()`. More than a nit. |
| **L34** | Cost defaults to `{0.0, 0.0}` so live reports read $0.00 | Partial from round 25. |
| **L8** | `Proposition.status` never updated from `candidate` | Partial from round 25. |
| **L29** | `accounting.py` docstring says nearest-rank percentile, code interpolates; `q` not range-checked | Partial from round 25. |
| **L38** | O(N×M) full-store scans in novelty `_gather_evidence` | Partial from round 21. |

Suggested next batch: **L33's three nits + L26**, then **L37 + L5**.

---

## 4. Traps and lessons learned

These cost real time. Read before editing.

**Editing pitfalls**

- **`acquisition_orchestrator` has two near-identical `for pi_id in candidate_ids:` blocks**
  (location resolution ~206, corpus building ~395). Anchoring on that line alone picks the
  wrong one. I destroyed the file twice this way and had to `git checkout` and redo it.
- **`s.index()` for an end marker matches an earlier occurrence.** Always pass a `start`
  offset: `s.index(marker, start)`. Same failure, twice, in the same file.
- **Never monkeypatch `pathlib.Path.stat`** — pytest's own reporting calls it and the test
  run dies with an internal error. Use a real sparse file (`f.truncate(60 * 1024 * 1024)`)
  to make a file "large" for free.
- **Check whether a finding is genuinely open before fixing it.** M59 had been fixed for
  20 rounds. Verify by reading the code, not the report.

**Fix-shape pitfalls (the fix that looks right but isn't)**

- **M37**: trimming `theme.statements` to a budget does nothing unless you also trim
  `theme.metadata["statement_ids"]` — the id list is what gets loaded, so the budget is
  bypassed. Found only because a test asserted the observable count.
- **M35**: merging identity groups pulls in members that were never loaded in this call, so
  `id_to_paper[pid]` raises `KeyError`; and the supersede list can name an identity twice,
  which produces a duplicate provenance edge and a `UNIQUE constraint failed` error. Both
  needed handling *after* the "obvious" merge fix.
- **M73**: two shapes. Twelve sites build the store inside `async def _run()`; three build
  it *outside* via `_get_artifact_store` and close it inside. Wrapping the outer scope on
  those three put `await` outside an async function.
- **M76**: forwarding `extra_plugins` is not enough — `_evaluation_config` dropped them on
  the config-file path too. The bug existed at two levels.
- **M53**: do **not** cache plugin discovery globally — a stale cache hides plugins
  installed later in the same process and breaks the existing discovery tests.

**Environment facts**

- **typer 0.27 has no `typer.Choice`, and `click` is not installed as its own distribution.**
  Use `typing.Literal` — typer renders it as a real choice (exit 2, lists valid values).
- **`loop.getaddrinfo` looks up `socket.getaddrinfo` at call time**, so existing tests that
  monkeypatch `socket.getaddrinfo` keep working after switching to the async form.
- **CliRunner tests do exist** (round 20): `tests/unit/test_cli_reporting_exit_codes.py`.
- **Live tests** (`tests/live/`, 21 tests) are excluded from the normal suite.

**Do not break these**

- **Equilibrium candidate selection**: selecting a *failed* candidate is load-bearing for
  the `eq-incorrect-llm-candidate` benchmark. Never make `_select_candidate` reject
  failed/pending candidates.
- **Don't add inert controls.** Three config knobs were *removed* in round 32 (M85)
  because they were stored and never read and had no loop to bound them. A parameter that
  reads like a working control but isn't is worse than no parameter. If a fix would be
  inert today, defer it and say why (this is why M48c is deferred).

**Deferring is a legitimate outcome.** Blocked/deferred items with written rationale:
L21 (timing schema can't express the decision), M48c, M62, M78. Report honestly rather
than shipping something that doesn't work.

---

## 5. Orientation

- `docs/CODE_REVIEW_2026-08-29.md` — the review. §1 summary, §4 Medium, §5 Low, §7 tooling,
  §9 triage, then round notes (newest first, immediately after §9.8).
- `src/research_harness/kernel/` — plugin kernel; `bootstrap.py` is the composition root.
- `src/research_harness/plugins/` — all plugins, grouped by domain.
- `src/research_harness/research/` — schemas, benchmarks, symbolic math.
- `tests/unit/`, `tests/integration/` — the suite. `tests/live/` needs an API key.
- Round 18+ test files are the best examples of the expected test style:
  `test_literature_semantics.py`, `test_cli_structure.py`, `test_bootstrap_config.py`,
  `test_m50_m64_sweep.py`, `test_literature_performance.py`, `test_round11_leftovers.py`,
  `test_m48_rate_limiting.py`, `test_low_l7_l23.py`.

**Verification tooling:** `uv`-managed `.venv`; run everything through `.venv/bin/`.
`pytest -x` is useful when triaging. There is no pre-commit hook.
