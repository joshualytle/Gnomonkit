---
name: folder-placement
description: Activates before creating a folder outside the current repo (worktree, clone, QA data, backup, download) or anywhere at a drive root, the home folder or the projects folder.
---

# Every folder you make has one home and a label

This tends to go wrong slowly. Each session makes "just one" folder where it
is convenient: test data at the drive root, a fresh-install copy beside it, a
clone per experiment in the projects folder. Over months they pile up, none
labelled, and telling the user's own folders from an agent's leftovers becomes
a manual job.

## Where things go

| What | Where |
|---|---|
| A worktree | The harness's own worktree feature when there is one (it keeps them under `.claude/worktrees/`). By hand: `<repo>.worktrees/<task>` beside the repo, never a new sibling `<repo>-<task>`. |
| QA data, test fixtures you generate, fresh-install or backup copies, captures | The scratch root: `python make_scratch.py "<purpose>" --days N` prints a new, labelled folder there. |
| Anything a later session must rerun (generators, data modules, release scripts) | The repo. Not scratch, and not the session scratchpad: temp folders get purged. |
| Secrets | Never scratch. A private location the user named. |

The scratch root is the core plugin's `scratch_root` option, else
`GNOMONKIT_SCRATCH_ROOT`, else `~/.gnomonkit/scratch`. If a project already
names its own scratch area, use that.

## Never

- **Never create a folder at a drive root** (`C:\`, `/`), in the home folder
  root, or directly in the projects folder unless the user asked for that
  exact path.
- **Never build a Windows path from a POSIX one by hand.** `/c/work/x`
  passed to a native tool from the wrong shell becomes `C:\c\work\x`.
- **Never leave a folder unlabelled.** If you had to make one somewhere else,
  stamp it: `python make_scratch.py --mark <folder> "<purpose>" --days N`.
  The label (`.gnomonkit-made.json`: by, purpose, created, expires) is what
  lets `local-compare` call it safe later instead of asking the user.

When the work is done, say what you left behind and where, so the user can
answer "remove it" in the same turn.
