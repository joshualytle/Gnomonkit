---
name: local-compare
description: Activates when the disk is full or messy, the user asks which local folders, clones or worktrees are needed or whether a copy matches GitHub, or before cleaning project folders.
---

> Full trigger text: Activates when the user says the disk is full, messy or "has copies everywhere", asks which local folders, clones or worktrees are still needed, asks whether a local copy matches what is on GitHub, before any cleanup of project folders, and when a session finds several copies of one repo side by side.

# Know what every local copy is before anyone touches one

Agents left to themselves fill a drive with copies: QA data folders at the
drive root, a pile of worktrees of one repo beside the projects folder, a
folder created by a POSIX-style path that Windows took as relative, and
worktrees in a purged temp folder still registered in their repos. None of it
is labelled. "Is this still needed" then takes a manual check of branch,
upstream, merge state and size for each folder, and the copies that matter
(uncommitted work, commits pushed nowhere) look exactly like the ones that do
not.

## Run the inventory first

```text
python local_inventory.py --json <scratch>/inv.json --page <scratch>/inv.html
```

(`local_inventory.py` sits beside this file.) Roots default to the folder
holding the current repo plus its drive root on Windows; pass `--root` for
others, or set them in `GNOMONKIT_CLEANUP_ROOTS`. It is read-only and takes a
minute on a large disk (`--no-size` skips sizes).

For every folder it reports the kind (clone, worktree, plain, container,
system), the branch, uncommitted and unpushed counts, whether the branch is
merged into the default branch (contained, patch-equivalent after a rebase or
squash, or upstream deleted), the size, whether a Claude session used it in
the last few hours, and plugin install records that point at folders that no
longer exist. Every worktree a clone registers is included wherever it lives,
which is how worktrees lost in temp folders turn up.

## Read the verdicts honestly

- **safe**: a worktree that is clean, pushed, merged and idle; a folder whose
  `.gnomonkit-made.json` has expired; a dead plugin record. Nothing is lost by
  removing it.
- **review**: everything that carries a risk or that nobody recorded making.
  "No record of who made it" is not "junk": it is the user's call.
- **keep**: primary clones, system and vendor folders, and anything the user
  marked keep before.

A worktree showing hundreds of uncommitted changes that are almost all
*deleted* files usually means its folder was emptied by a temp purge; say so,
and check whether its branch was pushed before calling it lost.

## Hand it over as a page

Publish the `--page` output the way `report-pages` says (the template is the
shared one). Every item that is not keep gets Remove / Keep / Look first.
Lead with the total that is safe to remove. Then act with `local-cleanup`.
Never remove anything from the table alone.
