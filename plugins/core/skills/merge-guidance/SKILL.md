---
description: Activates when handing the owner a PR to merge or review: say whether to merge now or wait, naming the check and why.
---

# Merge guidance always states the check policy

Every "merge this PR" instruction MUST
include one of:

- **"Merge now — no need to wait"** with the reason (e.g. docs/workflow/config
  only; the pending checks exercise code this PR doesn't touch; no required
  checks exist on the repo), OR
- **"Wait for `<check name>`"** with the reason that check actually validates
  THIS change (e.g. app code changed → analyzer + unit suite are the gate;
  a promotion into a public branch → the security/vuln stage gates are the
  point).

**Why:** repos without branch protection show an always-active merge button
next to a wall of pending checks; the owner can't tell which ones matter for a
given PR and wastes time waiting on irrelevant ones (or merges past relevant
ones).

**Who merges** follows the user's merge setting, the profile's `Merging:`
line (`delivery.merge`; default `agent`). With `agent`, a green PR of your
own is merged by you, not handed over: this guidance applies when the PR
is the owner's to merge, which is every PR under `ask`, the PRs that match
the `ask-for` list, and any PR a task brief reserves for someone else.

**How to apply:**
- Docs / CI-workflow / config-only PRs → merge now.
- PRs touching app/library code → wait for the analyzer + test checks.
- Release-gating or public-branch promotions → wait for the security gates.
- Name the check, never just say "wait for CI".
