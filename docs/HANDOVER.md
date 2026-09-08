# Handover — finishing the code-review remediation

**Repository:** `/home/vigor/agentwork/is-research` (`research-harness`)
**Handover date:** 2026-09-08 (updated same day, after rounds 35–38)
**HEAD at handover:** `9b8a235` — *docs: make the Medium tier an explicit out-of-scope boundary*
**Branch:** `main`, remote `origin` (`git@github.com:vigor-jzhang/is-research.git`)
**Working tree at update:** rounds 35–38 complete and verified, **uncommitted** (standing
instruction: do not commit unless asked).

---

## 1. What this work is

A full-repository code review was performed and written up in
[`docs/CODE_REVIEW_2026-08-29.md`](CODE_REVIEW_2026-08-29.md). It found **158 findings**:
8 Critical, 25 High, 86 Medium, 39 Low.

The dominant defect pattern in this codebase is **fail-open** — swallowed exceptions,
checks that cannot fire, and `None`/absent values scored as successes. In a research
harness (which is a measurement instrument) this silently manufactures scientific
claims, so the review's standard is: *when it fails it must fail loudly.*

Since the review there have been **38 fix rounds**. Critical, High and the Low tier are
fully closed. Read §1 (executive summary) and §6 (what the codebase gets right) of the
report first — §6 in particular explains the architecture you must not break.

### State at handover

| Tier | Total | Closed | Open |
|---|---|---|---|
| Critical | 8 | 8 | 0 |
| High | 25 | 25 | 0 |
| Medium | 86 | 83 | **3** (all deferred: M48 on M48c, M62, M78) |
| Low | 39 | 38 | **1** (L21, blocked) |
| **Total** | **158** | **154** | **4** |

The Medium tally was reconciled in round 35 (task zero): the §9.0 row had said 81/5
while the closed-ID enumeration said 83/3; by-ID truth is **83 closed / 3 open**, and
the row now agrees. Deferred findings count as open (L21 blocked ⇒ open, exactly like
M62/M78). The stale round-19 "Open Medium" list in §9.0 and §9.8's split were also
corrected. Twenty-one finding bodies closed in early rounds (M1–M16, M61, M77/M81/
M82/M86, L20) now carry their `**Fixed (round N).**` markers, each verified in code
first.

---

## 1.1 Scope boundary — hard limit

**The Medium tier is out of scope. Do not work on M62, M78 or M48c.**

| ID | Status | Why it is excluded |
|---|---|---|
| **M62** | **Do not touch** | Needs the user's decision on the session durability contract. A fix was implemented and reverted; redoing it without that decision just re-litigates it. |
| **M78** | **Do not touch** | Needs `OPENROUTER_API_KEY`. Nothing about it is fixable as a regression gate without one. |
| **M48c** | **Stays deferred** | M48a and M48b are done. M48c (a global concurrency cap) is inert until `src/` actually has concurrency. Do not implement it. |

**Scope is: clear the Low tier, then stop and report.** That was completed in rounds
35–38 (L33+L26, L37+L5, L10, then L34+L8+L29+L38). Every Low finding is closed, and
every remaining open item is blocked/deferred **with a written rationale** (L21, M48c,
M62, M78). Do not start M62 or M78 on your own initiative even if they look tractable.
**Ask first.**

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

Current baseline: **1409 passed**. A round is green only when all three are clean and the
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

### Medium — OUT OF SCOPE

See §1.1. **All three remaining Medium findings are excluded: M48 (on M48c), M62, M78.**
Reconcile the count as task zero so the report is honest, then leave them alone.

| ID | What's left | Why it's not just code |
|---|---|---|
| **M48** | **M48c only** — a global concurrency cap | Deferred deliberately. There is **no concurrency anywhere in `src/`** (no `gather`, no `Semaphore`, no `create_task`). A semaphore added now would be inert — a control that reads as working and does nothing. Add it when concurrency is added. M48a (rate limiting) and M48b (locked pinned-address map) are done. |
| **M62** | Wildcard session subscriber blocks on a file append per event | Needs the user's decision: "every event is on disk when the run returns" and "persistence off the critical path" cannot both hold without an explicit flush point callers must honour. A queue + background writer was implemented and reverted — it broke `test_e2e_mocked_run`. |
| **M78** | Live suite can't gate anything | Needs `OPENROUTER_API_KEY`. Not fixable as a regression gate. |

### Low — cleared in rounds 35–38

Every Low finding is now closed: **L33** (three remaining nits) and **L26** (round 35),
**L37** and **L5** (round 36), **L10** — both halves; the retry-orphaning half the
previous handover set aside was fixed by validating the whole assembly response before
the first store write, no rollback needed — (round 37), and the four partials **L34,
L8, L29, L38** (round 38). The only Low not closed is **L21**, blocked since round 23
(the timing schema cannot express the decision). See the round notes in the report for
what each fix did and what was deliberately left alone.

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

**New pitfalls found in rounds 35–38**

- **CliRunner tests must be sync.** The CLI commands call `asyncio.run()` internally;
  invoking them from inside an `async def` test (pytest-asyncio) dies with
  "asyncio.run() cannot be called from a running event loop". Seed stores with
  `asyncio.run()` in a sync test, then invoke.
- **`ArtifactEnvelope.create` rejects a plain dict payload** (payload is typed
  `BaseModel`). To simulate a *malformed stored artifact*, build it the way
  `SQLiteArtifactStore._row_to_envelope` does: `ArtifactEnvelope[Any](...)` with a dict
  payload and a `compute_content_hash` of that dict.
- **pyright: assigning `Any` to a declared `x: list[T] | None` re-widens x after an
  `is None` narrowing.** Build a separate local via a ternary instead of reassigning.
- **pytest-asyncio is in `auto` mode** — an explicit `asyncio.run()` wrapper around a
  coroutine inside an async test is redundant; just await it.

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
  `test_m48_rate_limiting.py`, `test_low_l7_l23.py`, and the round 35–38 files
  `test_low_l33_l26.py`, `test_low_l37_l5.py`, `test_low_l10_results_assembler.py`,
  `test_low_partials_l34_l8_l29_l38.py` (the last reuses fixtures from
  `test_evaluation_harness` / `test_scientific_core_followon` / `test_novelty` /
  `test_results_assembly` via the importable `tests.unit` package).

**Verification tooling:** `uv`-managed `.venv`; run everything through `.venv/bin/`.
`pytest -x` is useful when triaging. There is no pre-commit hook.
