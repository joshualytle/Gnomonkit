---
description: Activates before any commit or push, when choosing where work lands, adding a deploy script, handling a green open PR, or opening a PR on an unmerged one.
---

> Full trigger text: Activates before any commit or push, when choosing where work should land, when adding any script or command that deploys, when a PR of yours is green and still open or a merge is refused for a permission reason, when about to open a PR on top of an unmerged one, and when handing the owner a PR to merge or noticing stale remote branches. Head branches die with their PR (a scheduled cleanup with a keep list; the auto-delete setting only once long-lived branches are protected). Especially when the target branch is one a host deploys from.

# Ship through PRs, never straight to the deploy branch

On a host that deploys on push, committing to the production branch does not
just skip review — it **publishes**. There is no window in which someone can
catch it, and the rollback is a second public deploy.

## The rule

- **Feature branches: `claude/<description>`.** One arc per branch.
- **Never commit or push directly to a protected/production branch.** Work
  lands through a PR whose preview build is the verification.
- **Preflight before committing**, not five times across the session: the
  project's typecheck and build must be clean. Fix, then commit.
- **Never add a bypass path.** A manual deploy command sitting next to a
  push-to-deploy pipeline is a footgun: running it ships an unreviewed build
  past the PR, and possibly to a stale target. If one exists, delete it rather
  than documenting around it.
- **Conventional Commits**; body explains the *why*, since the diff already
  shows the what.

The project's `CLAUDE.md` carries the parameters — which branch is protected,
what the preflight command is, and what a push actually triggers.

## Merge your own PR when it is green; never stack on an unmerged one

The failure this prevents: each session parks its PR "for the owner to
merge" and opens the next session's work on top of it. By the time the
owner merges, the stack is long; the first PR lands, the second conflicts
with the base on a file a later base-branch merge touched, and every PR
above it goes red with it, even when `git merge-tree` shows the top of the
stack merging cleanly (a host's merge handles a criss-cross base worse than
git does). The owner would far rather be told "I can't merge this" than
inherit a stack.

- **Merge each PR yourself as soon as its checks are green.** A PR that is
  complete and green is not finished until it is in the base branch.
- **The user's merge setting decides who merges.** It reaches the session as
  the profile's `Merging:` line (`delivery.merge`, docs/PROFILES.md); with no
  line, the default is the one above. `agent`: merge your own green PR
  without asking. `ask`: ask the user before merging any PR, green or not,
  and stop there until they answer; do not open the next PR on top in the
  meantime. `ask-for`: merge your own, except a PR whose changed files match
  one of the listed path globs or which carries a listed `label:<name>`;
  for those, ask (check with `gh pr view <n> --json files,labels`). A task
  brief that says otherwise ("PR only, the primary merges") wins over the
  setting for that task.
- **If the merge is refused** (a permission rule, a mode that blocks
  merges), **say so in that same turn** and hand the owner the exact
  command. Do not open the next PR on top of the unmerged one as a
  workaround: one open PR is the normal state, two is the limit.
- A stack is a *review* shape within one session, fine while the bottom
  merges the same day; it is never a parking lot across sessions.
- **Recovery when a stack has already gone red:** merge the base branch
  into the top branch locally (git's recursive merge resolves what the
  host could not), push, retarget the top PR to the base, merge it; the
  host then marks the PRs underneath merged by itself once their heads are
  ancestors of the base.

## Head branches die with their pull request

Without a mechanism, merged `claude/*` and `chore/*` branches pile up on
the remote because nothing removes them, and the owner tends to notice
before the agent does.

- The repository setting "Automatically delete head branches" (Settings →
  General; API field `delete_branch_on_merge`) is only safe **once every
  long-lived branch is protected against deletion**. It deletes the HEAD of
  any merged PR, and a promotion chain (`dev → test → beta → main`) makes a
  pipeline branch the head of every hop — on a plan without branch
  protection it would delete `test` and then `beta` on the first
  promotions after it is switched on.
  Verify protection with `gh api repos/<o>/<r>/branches/<b>/protection`
  returning a rule, not Not Found, before recommending it; until then the
  cleanup workflow below, with an explicit keep list, is the only mechanism.
- Add a scheduled cleanup workflow (weekly + `workflow_dispatch`, with a
  dry-run input) that deletes branches whose PR is merged, or closed and
  older than a week, and never touches pipeline branches (`main`, `dev`,
  `test`, `beta`) or a branch with no PR. The setting only covers merges
  made after it is switched on and only through the merge button; the
  workflow catches the rest.
- When you hand the owner a PR to merge, the head branch's fate is part of
  the handover: it should be gone after the merge, and a stale list at the
  next session start is a bug in this process, not a chore for the owner.
- Bulk branch deletion is exactly the kind of destructive batch the agent's
  permission layer blocks; when it does, do not loop around it — ship the
  workflow and let its first dispatch do the deleting under repository
  permissions.

## When NOT to use this

Scratch repos, or when the owner explicitly asks for a direct commit. Say that
is what you are doing rather than doing it silently.
