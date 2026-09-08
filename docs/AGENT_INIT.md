# Init instruction for the incoming agent

Copy everything below the line as the agent's starting prompt.

---

## Goal

Finish the code-review remediation for the `research-harness` repository at
`/home/vigor/agentwork/is-research`. The review is
`docs/CODE_REVIEW_2026-08-29.md` (158 findings). Critical and High are fully closed;
**13 findings remain — 5 Medium and 9 Low** (the two numbers in the report disagree; see
below). Your job is to close as many as are genuinely closable, and to leave an honest
record of anything that cannot be closed.

**Start by reading `docs/HANDOVER.md` in full.** It has the current state, the working
loop, the report-maintenance steps, the remaining work itemised, and a long list of traps
that have already cost time. Do not skip it. Then read §1 and §6 of the review report.

## Task zero (do this before any code)

The §9.0 tally row says *81 Medium closed / 5 open*, but the closed-ID enumeration in the
same section sums to **83 closed / 3 open** (M48 on M48c, M62, M78). Re-derive the Medium
count by finding ID and correct whichever number is wrong, so the rest of your work starts
from a true baseline.

While you are there: many finding bodies in §4/§5 were never marked even though the work
was done (M61 was fixed in round 29, M77/M81/M82/M86 in round 14, L20 in round 18). The
absence of a `**Fixed (round N).**` marker does **not** mean a finding is open —
cross-check the round notes and the §9.0 enumeration before touching anything.

## Then work in rounds

One round = a small coherent set of findings. For each:

1. **Verify each finding is actually open** by reading the code. M59 sat in the report as
   open for 20 rounds after it had already been fixed.
2. Fix it.
3. Verify (this exact order — stale `.pyc` has masked reverted code before):

   ```bash
   find . -path ./.venv -prune -o -name "__pycache__" -type d -print0 | xargs -0 rm -rf
   .venv/bin/ruff check src tests
   .venv/bin/pyright
   .venv/bin/python -m pytest tests/unit tests/integration -q -p no:cacheprovider
   ```

   All three must be clean and the suite must show **zero failures**. Baseline: 1393 passed.
4. **Prove every new test fails before the fix**: `git stash push -- src/`, run the new
   tests (expect failures), `git stash pop`, run again (expect pass). Clear `__pycache__`
   on both sides. Note that untracked files are *not* stashed, so a test for a brand-new
   source module proves nothing. If a test passes both ways it is worthless — rewrite it.
5. Update `docs/CODE_REVIEW_2026-08-29.md`: mark the finding bodies, update the progress
   table and the §9.0 tally (row **and** closed-ID prose must agree), and add a
   `### Round N notes (...)` section before the previous round's notes.
6. Report back and **wait**. Do not commit unless asked.

## Suggested order

1. **L33** — its three remaining nits (`runtime inspect` "Services" heading, 13
   `__import__` hacks, dead `or` branch) plus **L26** (frozen dataclass with mutable
   fields; shared criteria instances). Both mechanical.
2. **L37** (empty `plugins` list yields a silently inert runtime) plus **L5**
   (`evidence_artifact_ids` degenerates to "all artifacts").
3. **L10** — but read the handover first; it may need `assemble()` restructuring and may
   be better deferred with a written reason.
4. Then the partials: **L34**, **L8**, **L29**, **L38**.

## Rules

- **Never** make a change that is inert today. If a fix would be a control that reads as
  working but does nothing, defer it and write down why (that is exactly why M48c is
  deferred — there is no concurrency in `src/` for a semaphore to bound). Three such knobs
  were deleted in round 32 rather than left as decoration.
- **Never** make equilibrium `_select_candidate` reject failed/pending candidates — the
  `eq-incorrect-llm-candidate` benchmark depends on selecting a failed candidate.
- Deferring with a written rationale is a valid outcome (L21, M48c, M62 and M78 are all
  deferred for real reasons). Report honestly rather than shipping something that doesn't
  work.
- **Hard boundary: the Medium tier is out of scope.** Do not work on **M62**, **M78** or
  **M48c**, and do not start them on your own initiative even if they look tractable.
  - M62 needs the user's decision on the session durability contract. A fix was implemented
    and reverted; redoing it without that decision just re-litigates it.
  - M78 needs an `OPENROUTER_API_KEY`; nothing about it is fixable as a regression gate.
  - M48c (a global concurrency cap) is inert until `src/` has concurrency. Leave it
    deferred. M48a and M48b are already done.
  - If you believe one of these has become tractable, **stop and ask** rather than
    starting.

## Definition of done

Clear the Low tier, then stop and report. Every Low finding is either closed with a green
suite and a pre-fix-failing test, or explicitly recorded as blocked with the reason written
into the round notes. The Medium tier is untouched apart from reconciling the §9.0 count in
task zero. The tally in §9.0 is internally consistent and matches reality.
