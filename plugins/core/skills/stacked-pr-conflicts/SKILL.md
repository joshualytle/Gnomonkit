---
description: Activates when handing over two or more PRs, opening a PR onto a branch with open PRs, or when a merge the owner was told was ready conflicts.
---

> Full trigger text: Before handing the owner more than one PR to merge, test-merge the whole batch into the target branch in order and verify the combined result — per-PR CI structurally cannot see a conflict between two green PRs, or a stack that is individually green and collectively broken. Activates when handing over two or more PRs, when opening a PR onto a branch that already has open PRs, and when a merge the owner was told was ready hits a conflict.

# A batch of green PRs is not a mergeable batch

Per-PR CI runs each branch against the target as it was **when that branch
started**. It never runs it against the tree that exists once the branch's
siblings have landed. So two PRs that each add a row to the same index table
are both green, and the second one to merge conflicts — which the owner
discovers mid-merge, after being told the batch was ready.

The conflict is not the failure. Being told the batch was ready is the failure.

A typical case: a batch of PRs is handed over as green, and two of them each
add a line to the same docs index table.

## The rule

**Before handing over two or more PRs, test-merge them all into the target
branch, in order, in a throwaway worktree.** Hand over the merge order the
sweep prints, oldest first.

For a stack more than two or three deep, also **build and test the combined
result**. Individually green and collectively broken is the second failure this
catches, and it is the more expensive one: nothing in the per-PR signal hints
at it.

```bash
git worktree add -q --detach /tmp/sweep origin/<target>
for b in $BRANCHES_OLDEST_FIRST; do
  git -C /tmp/sweep merge --no-ff -m "sweep: $b" "origin/$b" \
    || echo "CONFLICT $b: $(git -C /tmp/sweep diff --name-only --diff-filter=U)"
done
# then, for a deep stack, run the project's build and test suite in /tmp/sweep
git worktree remove --force /tmp/sweep
```

Nothing is pushed and the working tree is untouched, so this is cheap enough
to run every time rather than when it feels risky.

## When it conflicts

Resolve **on the PR branch**, not in the sweep worktree:

```bash
git checkout <branch> && git pull origin <target>   # resolve, commit, push
```

Then re-run the sweep before saying anything is ready. A batch reported clean
once and merged an hour later is a batch nobody swept.

## What makes a batch likely to collide

Worth a second look even when the sweep is clean, because these are where the
next one will come from:

- An index, registry, or table of contents that every feature appends a row to
- A version constant, changelog, or migration list with one entry per change
- A generated file both branches regenerate
- A shared config or dependency manifest

## When NOT to use this

A single PR — per-PR CI is sufficient and the sweep tells you nothing. Also
skip when the branches are known to touch disjoint trees and the owner is
merging one of them now; say that is the reason rather than skipping silently.

Pairs with `merge-guidance`, which governs what to say about **one** PR's
checks. This governs what to check before speaking about **several**.
