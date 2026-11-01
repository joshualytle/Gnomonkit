---
name: local-cleanup
description: Activates when removing local folders, clones or worktrees, running git worktree remove/prune, freeing disk space, or after the user answers a local-compare page.
---

# Clean up reversibly, and only what was approved

The inventory (`local-compare`) says what each folder is. This skill acts on
the user's verdicts. Two failures it exists to stop: deleting a worktree
whose only copy of a commit was local, and a cleanup that cannot be undone
when a "junk" folder turns out to hold someone's test data.

## The procedure

1. **Inventory first**, fresh: `python ../local-compare/local_inventory.py --json inv.json`.
2. **Get verdicts.** Read the page's answers (its answers store, or the
   pasted `FEEDBACK:` block) into a file.
3. **Dry run**, and show the user the plan:
   ```text
   python local_cleanup.py plan --inventory inv.json --answers answers.json
   ```
   `--safe` takes every item the inventory called safe with no page; use it
   only when the user asked for exactly that ("clear out the merged worktrees").
4. **Apply** the same command with `--apply`. Report the batch id.

`local_cleanup.py` re-inspects each item before acting and skips any that
changed since the inventory. It refuses clones, containers and system
folders: a primary clone is removed by hand, after looking.

## What removal means

- **Worktree**: `git worktree remove`, never `--force`. If git refuses
  because of uncommitted files, the folder goes to holding instead and the
  registration is pruned. A detached HEAD with unpushed commits gets a
  `gnomonkit-rescue/<name>` branch first. A branch is deleted only when it
  is merged, and never with `--keep-branches`: use that in repos whose rules
  forbid deleting a branch the session did not create.
- **Plain folder**: moved to holding, on the same drive, so it is a rename
  and not a copy.
- **Dead plugin record**: dropped from `installed_plugins.json`, which is
  backed up to holding first.

Holding lives in `~/.gnomonkit/holding/<batch>/` (or `<drive>/.gnomonkit-holding/`
for another drive). `local_cleanup.py list` shows it, `restore <batch> [name]`
puts things back. **`purge --older-than 14 --apply` is the only permanent
delete**: run it when the user asks to reclaim the space, after showing its
dry run.

Do not reach for `rm -rf`, `Remove-Item -Recurse` or `git worktree remove
--force` to get past a refusal. The refusal is the point; see
`enumerate-before-destroying`.
