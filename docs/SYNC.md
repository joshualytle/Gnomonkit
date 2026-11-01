# The sync gate

A plugin update reaches your machine the moment you update its marketplace, and plugins run hooks on
every tool call. Nothing in that path checks who made the update, so one bad push to a marketplace (a
stolen token, a compromised collaborator) would run on every machine that takes it.

The sync gate puts a check in that path. Claude Code reads a gated marketplace from a local copy the
gate controls, pinned at a commit. The gate fetches updates into quarantine, moves the pin on its own
only when every new commit is signed by a key you approved and no newcomer touched code, and holds
anything else for you to review. You approve a held update by merging a pull request in your own
overlay repository.

## Put a marketplace under the gate

```sh
gnomonkit sync adopt <marketplace>
```

`<marketplace>` is the name `claude plugin marketplace list` shows (for Gnomonkit, `gnomonkit`). Adopt:

1. fetches the marketplace's repository into quarantine (`~/.gnomonkit/sync/quarantine/<marketplace>.git`);
2. pins the commit your plugins are installed from now, and checks it out to
   `~/.gnomonkit/sync/approved/<marketplace>/`;
3. checks the repository's `marketplace.json` declares the same name, so plugin keys such as
   `core@gnomonkit` stay the same, and that every plugin it lists comes from a path inside the
   repository (a plugin taken from another repository or a URL could not be pinned, so adopt refuses);
4. prints the commands that point Claude Code at that copy. **It runs none of them.**

Run the printed steps yourself, in this order:

1. `claude plugin marketplace remove <marketplace>`. This **uninstalls every plugin from that
   marketplace** in every scope; the next steps put them back.
2. `claude plugin marketplace add "<the approved copy>"`.
3. `claude plugin install <plugin>@<marketplace> --scope <scope>` for each plugin, with the scope it had
   (adopt lists them). A project-scope install is run in that project and writes its
   `.claude/settings.json`: run `git diff` there afterwards and keep or revert the change on purpose.
4. `/reload-plugins` in each open session.

Running `sync adopt` again prints the steps again and changes nothing. `--branch` follows a branch other
than the remote's default. `--url` (when Claude Code's record names no remote), `--pin` (when no
installed commit is recorded) and `--force` (adopt again over an existing pin) work only in a terminal,
because each one decides what you trust.

## Set your overlay

Approvals and approved keys live in your private overlay repository, so they are reviewed, dated and
follow you between machines. Tell the gate where it is, in a terminal:

```sh
gnomonkit sync overlay set <remote> [--github <owner>/<name>] [--branch <branch>]
gnomonkit sync overlay show
```

The setting is kept in `~/.gnomonkit/sync/config.toml`, not in your profile, because an agent may edit
the profile; `overlay set` and `overlay clear` refuse to run without a terminal. The gate reads two
files from the overlay:

| File | What it holds |
|---|---|
| `sync/signers/<marketplace>.allowed_signers` | the keys whose signed commits move the pin on their own, one line each in ssh-keygen's ALLOWED SIGNERS format |
| `sync/approvals.toml` | one `[[approval]]` per held range you approved |

The gate reads both **from the overlay's default branch as fetched from its remote**, never from a
working tree. Editing a file on disk, or committing it without pushing, approves nothing. The branch is
fetched by name (the remote's default, or `--branch`), never as whatever `HEAD` points at, and a local
`<remote>` must be a bare repository: `overlay set` refuses a working clone, because a commit on any of
its branches, pushed or not, would otherwise count. Give the remote the clone pushes to instead.

If the overlay can't be fetched, nothing moves: a newer upstream is held with the reason **the overlay
could not be fetched**, even when it is signed, until the fetch works again.

When `gnomonkit` is adopted with an overlay set, adopt drafts the pull request that adds the release key
Gnomonkit suggests (`plugins/core/sync/suggested.allowed_signers`) to your overlay; set the overlay later
and `gnomonkit sync approve-draft gnomonkit-signers` drafts it then. Check the key's fingerprint against the
one the maintainers publish, then merge it.

## Checks

```sh
gnomonkit sync check [--source <marketplace>]
gnomonkit sync status
```

A check runs at session start (in the background, at most hourly), nightly with the portal's refresh
when the portal is installed, and whenever you run it. For each source it:

1. **fetches into quarantine**: `git fetch` into a bare repository with hooks pointed at an empty
   folder, no checkout, no submodules, no `ext::` transport, and nothing run from the fetched tree;
2. **decides** over the commits from the pin to the fetched head. The pin moves on its own when both
   hold:
   - every commit is signed with an SSH key in your approved signers (`git verify-commit` against that
     list). The verdict depends on that list alone: the check runs with your git config and any
     `GIT_CONFIG_*` variables switched off, with ssh-keygen from `PATH` as the verifier, and OpenPGP and
     X.509 signatures count as unsigned, so no keyring you trust can pass a commit. Every commit from
     `git rev-list` is checked; if one can't be read the whole range is held;
   - no commit comes from an author or committer absent from the history before the pin and touches
     code. Code is everything except `docs/`, `assets/` and Markdown files, but Markdown under a
     `skills/` folder and `CLAUDE.md` / `AGENTS.md` count as code, because they change what a
     session does;
3. **advances or holds.** On an advance the approved copy moves to the new head and the usual update
   notice offers the new version. Otherwise the update is held. A branch rewritten so the pin is no
   longer in its history is always held, until you approve that exact range.

The approved copy is checked out with `--force` and cleaned (`git clean -ffdx`) whenever it has moved
or differs from the pin, so an edit made to it on disk is undone and a file added to it, ignored or not,
is removed. The check lists what it put back (Python's own `__pycache__` is removed without a mention).

## Held updates

A held update is `~/.gnomonkit/sync/held/<marketplace>-<commit>.json`: the range, each reason (a commit
is unsigned, signed by a key you have not approved, or a newcomer changed these paths) and a file-level
summary of the change.

- The session-start notice says **update held for review**, with the kinds of reason and their counts
  ("2 unsigned commits, 1 new contributor touching code") and the `sync explain` id. It carries no text
  from upstream (names, e-mail addresses, paths, messages), because an agent reads it, and it prints
  **no** install commands.
- `gnomonkit sync status` lists what is held; `gnomonkit sync explain <id>` gives each reason, the files
  changed, and the `git log -p` command that shows the full diff from quarantine.
- The portal's **Updates** tab lists each source, its pin, and what is held with the reasons and the
  files changed (never file contents).

## Approving

Approval is a merged pull request in your overlay. Draft it from a terminal:

```sh
gnomonkit sync approve-draft <id>              # approve this one range
gnomonkit sync approve-draft <id> --signers    # trust the key(s) that signed it, for this and later updates
gnomonkit sync approve-draft gnomonkit-signers # the release key Gnomonkit suggests
```

or with **Draft approval** in the portal's Updates tab. Either one pushes a `gnomonkit-sync/<id>` branch
to your overlay and opens the pull request (on GitHub, with `gh`; elsewhere it prints the branch and you
open it). Read the change with `sync explain`, then **merge the pull request: that is the approval.**
Closing it changes nothing. The next check picks the approval up from the default branch.

The Draft approval button exists only when the portal runs in plain loopback mode, on the machine you
are at. Behind a token or a reverse proxy the tab is read-only and gives the terminal command instead;
a static snapshot serves no endpoints at all.

An approval entry looks like this:

```toml
[[approval]]
source = "examplemkt"
from = "<the pinned commit>"
head = "<the head commit you approved>"
reasons = ["commit 0123abc is not signed"]
drafted = "2026-01-02T03:04:05Z"
```

It releases updates up to `head`, as long as `head` is still in the source's history. An approval can
cover part of a held range (the pin moves to its `head` and the rest stays held), and its `from` may be
an earlier commit than the pin. A check that moved the pin this way says **moved by your overlay's
approval**.

## Protect the overlay

Merging is the approval, so make merging something only you do. On the overlay's default branch:

- require a pull request before merging, and block force pushes and deletions;
- don't give an agent's token the rights to bypass that protection or to change it;
- if others can push to the overlay, require a review from you.

## The guard, and its limits

core's `sync_guard.py` hook stops an agent from approving for you. It denies:

- any tool write under `~/.gnomonkit/sync/` (the approved copy, quarantine, held records, the overlay
  setting), including shell commands that write, delete or redirect there; reading is allowed, and so
  is the `gnomonkit sync` command. Paths are read the way the shell would: quotes, `~`, `$HOME` and
  `GNOMONKIT_HOME` variables, `.` and `..`, the call's working folder, and any `cd`, `pushd` or
  `Set-Location` before the write;
- `gh pr merge` on your overlay of a pull request whose branch is `gnomonkit-sync/*` or that changes
  anything under `sync/`. The guard looks the pull request up with `gh pr view --json
  headRefName,files,changedFiles` and denies the merge when the lookup fails or lists fewer files than
  the pull request changes. The repository is taken from `-R`/`--repo` (attached or separate, with or
  without a host or URL), `GH_REPO` (set before the command, exported earlier in it, or in the
  environment), or any GitHub remote of the working folder; a repository named through a variable it
  can't read is treated as the overlay;
- a `gh api` merge call aimed at the overlay (`pulls/<n>/merge` is looked up the same way), and any
  GraphQL merge mutation while an overlay is set;
- `git merge` or `git pull` of a `gnomonkit-sync/*` branch, and a push that maps one onto another branch.

It fails closed on those targets and open otherwise. It is a guard, not a sandbox: a program started
outside the agent's tools, a `git push` straight to the overlay's default branch, or a command written to
slip past its patterns, is not stopped. The merge on GitHub and branch protection on the overlay are the
real control.

## Without an overlay

With no overlay set, nothing can be approved and nothing is held. Updates to `gnomonkit` are checked
against the release key Gnomonkit suggests, on signatures alone: a release signed by it is offered as usual, anything else is
reported as **not offered**, with the reason. Other gated sources need an overlay with approved signers
to move at all.

## Key rotation

A new release key reaches you as a drafted pull request: a release signed by the current key adds the
new one to core's suggested key file, and once you have updated core to that release the next check
offers it (`sync status`, the portal's
"Release key to approve", `sync approve-draft gnomonkit-signers`). The old key stays approved until you
remove its line from `sync/signers/gnomonkit.allowed_signers` in your overlay, through a pull request of
your own. If the maintainers announce that a key is compromised, remove it straight away; updates signed
by it are then held.

## Files

| Path under `~/.gnomonkit/sync/` | What it is |
|---|---|
| `config.toml` | the overlay setting (`sync overlay set`) |
| `sources.json` | each gated source: its remote, branch, pin and the last check's result |
| `quarantine/<marketplace>.git` | the bare repository updates are fetched into |
| `approved/<marketplace>/` | the copy Claude Code reads, at the pin |
| `held/<id>.json` | one held update each |
| `overlay.git` | the fetched copy of your overlay, read from its default branch |
