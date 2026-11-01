---
description: Activates on 'review this', 'any bugs here', 'is this right', before a feature-labelled commit, or when reading someone else's branch.
---

# Reviewing a diff

The goal is to catch the bug, the swallowed error, and the broken invariant —
**not to rewrite the change.**

## How to run it

1. `git diff` for uncommitted work, `git diff <base>...HEAD` for a branch. For a
   large diff, delegate the read and have findings reported as `file:line`, then
   review the flagged hunks yourself.
2. For each finding: the `file:line`, the problem in **one sentence**, and where
   the fix belongs.
3. **Separate must-fix from nice-to-have.** A bug, a broken invariant, or a
   silently swallowed failure blocks. Naming and taste do not.

## The silent-failure checklist

These are what review catches and tests usually do not:

- **Swallowed errors.** A discarded `Result`, a `catch` that logs nothing and
  returns a default, an unwrap-or-default hiding a real failure. If a failure can
  be absorbed unnoticed, surface it or comment why silence is correct.
- **Panics on a critical path.** In a long-running service a panic is an outage
  nobody notices — the worst failure mode, because the operator believes they
  have coverage.
- **Off-by-one and boundary** in anything arithmetic, especially clamps.
- **Ambient time.** A clock read inside logic that needs testing; time going
  backwards; timezone and DST boundaries.
- **Integer overflow** in durations, counters, or budgets. Debug panics, release
  wraps — and a wrapped counter frequently reads as *unlimited*.
- **New logic without a test in the same change.**

## Report a clean review as clean

**If nothing is wrong, say so plainly.** A clean review is a valid result, not a
prompt to invent findings. Manufactured nitpicks train the owner to skim reviews,
which costs you the one that mattered.

Output shape:

```
## Review - <branch> @ <sha>

**Must-fix**
- path/file.rs:88 - one sentence on the defect. (owning area)

**Nice-to-have**
- path/file.rs:12 - one sentence.

**Clear:** <what you checked and found nothing wrong with>
```

Naming what you checked and found clean is as useful as the findings — it tells
the owner what the review actually covered.

## When NOT to use this

Reviewing your own work-in-progress before it is finished. Run it at the commit
boundary, not continuously.
