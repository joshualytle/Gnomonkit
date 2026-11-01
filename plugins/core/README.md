# core

> Provided as is, without warranty; see the [disclaimer](../../DISCLAIMER.md). The safety hooks are
> heuristics: they catch some mistakes and miss others, and they are not a sandbox.

The shared working discipline in Gnomonkit: safety hooks plus skills that load when their situation
comes up.

## Hooks

| Hook | Fires on | What it does |
|---|---|---|
| `destructive_interlock.py` | Bash, PowerShell | Blocks irreversible commands (pool/filesystem wipes, raw disk writes, volume and database drops, restores over live data, recursive deletes, force pushes, bucket and branch deletes) until the target has been enumerated. Surfaces matching lessons from your own Claude Code memory files. Override per command with `# interlock-ok: <what you enumerated>`. |
| `heredoc_guard.py` | Bash | Blocks heredocs containing backslashes, whose escapes don't survive the tool-call-to-shell path; write the content to a file and run it instead. |
| `grants_guard.py` | Write, Edit, MultiEdit, NotebookEdit, Bash, PowerShell, SendMessage | Keeps an agent from changing its own permissions or acting on other sessions without a grant: blocks a write to the orchestration grants (`~/.gnomonkit/grants.toml`, any file named `grants.toml`, its backup and temp files; relative paths, `..`, symlinks and hard links resolved) or to Claude Code's trust record (`~/.claude.json`), or to anything in Gnomonkit's folder-trust records (`~/.gnomonkit/trust/`: the repositories it trusted, which the session-start hook puts back, its backups and log), or to the usage allocation (`usage-allocation.toml`) and its cache; a shell command, read word by word the way the shell would, that names any of them (reading included: use `gnomonkit grants list`, `gnomonkit trust status`), runs the `gnomonkit grants`, `trust`, `usage` and `setup security` subcommands that write them, or sets or clears the variables the grants check reads; and a `claude --bg|stop|kill|rm|attach|respawn` command or a SendMessage to another session that the grants don't allow. Deny-only, no override marker. Fails closed when it can't check a call that touches any of this, open otherwise. A guard, not a sandbox: see [what is enforced](../../docs/ONBOARDING.md#what-is-enforced). |
| `skill_router.py` | Write, Edit, Bash, PowerShell (before and after) | Suggests the skill that applies when a command or path matches its trigger (`triggers.yaml`). Never blocks; fires once per skill per phase per sitting so it stays a prompt, not a nag. |
| `session_profile.py` | SessionStart | Injects your profile: personal (the `personal_profile` setting, else `~/.gnomonkit/personal.toml`; no overlay needed) plus this machine's (`~/.gnomonkit/machine.toml`). On a new machine it detects OS, shells and GPU and asks only what it can't detect, plus whether it is a personal or a work machine (once per machine, kept only in the machine profile). With no personal profile anywhere it offers the one-time first-run setup (`onboarding`), on a fresh start only and at most twice, never in dispatched (`GNOMONKIT_NO_ONBOARDING`) or remote sessions. Until the disclaimer is acknowledged it carries one line linking it. See [docs/PROFILES.md](../../docs/PROFILES.md). |
| `session_skill_map.py` | SessionStart | Injects a map of the installed skills grouped by the situation they belong to (`skill_map.yaml`). |
| `trust_autodiscover.py` | SessionStart (startup) | For each root you set to the `trusted` folder-trust level with `auto` on, marks the git repositories directly under it trusted in Claude Code when their `origin` owner is in the root's `owners` (one line per repository), and puts back a key Gnomonkit set there that Claude Code's file has since lost. Checks each repository again right before the write (a folder that has become a link, or one that no longer qualifies, is left alone), writes only `projects["<repository root>"].hasTrustDialogAccepted`, keeps a backup, writes nothing if the file changed meanwhile or has a format it doesn't recognise, and logs each change to `~/.gnomonkit/trust/audit.jsonl`. Skips roots whose folder hasn't changed; does everything, the write included, within 5 seconds and leaves the rest for the next start; shows at most 10 lines and a count; never blocks. `GNOMONKIT_TRUST_AUTO=off` turns it off. See [folder-trust levels](../../docs/ONBOARDING.md#folder-trust-levels). |
| `plugin_freshness.py` | SessionStart (startup, resume), UserPromptSubmit | Says in one line, at start and in sessions already running (re-checked in the background at most hourly, never on the prompt path), when an enabled plugin from a GitHub marketplace is behind the marketplace's default branch, and how to apply it without a new session (`claude plugin marketplace update` and `claude plugin update` in a terminal, then `/reload-plugins`). Uses `gh` when present (private repos), else the public raw URL. Fail-soft and silent when offline; `GNOMONKIT_FRESHNESS=off` turns it off. A marketplace under the sync gate is reported from the gate instead: an approved update is offered, a held one says *update held for review* with the reason and no install commands, and the gate's own check (`gnomonkit sync check --if-due`) is started in the background at most hourly. See [docs/SYNC.md](../../docs/SYNC.md). |
| `sync_guard.py` | Bash, PowerShell, Write, Edit, MultiEdit, NotebookEdit (before) | Stops an agent approving a sync-gate update for you: blocks tool writes under `~/.gnomonkit/sync/` (reads and the `gnomonkit sync` command are allowed), merging a pull request on your overlay through `gh pr merge` or `gh api` when its branch is `gnomonkit-sync/*`, it changes `sync/`, or `gh pr view` can't say (the repository read from `-R`/`--repo`, `GH_REPO` or the folder's remotes), and merging or pulling such a branch with git. Paths are resolved as the shell would (quotes, `~`, `.`/`..`, the working folder, `cd`). Fails closed on those targets and open otherwise; a guard, not a sandbox. See [docs/SYNC.md](../../docs/SYNC.md#the-guard-and-its-limits). |
| `context_watch.py` | UserPromptSubmit | Reads the last turn's token usage from the tail of the transcript and, when the main context reaches the `context_budget` setting (default 250k tokens; 0 turns it off), tells the session to finish the current step, write a handoff brief, then start a fresh session from it (session-dispatch handoff) or `/compact`. Once per crossing and again every further 100k. Also counts the session's compactions: one is fine; at `max_compactions` (default 2; 0 turns it off) it asks Claude to write the handoff (`session-close`) and tells you a fresh session from it is recommended, once per count. Never blocks. |
| `page_nudge.py` | UserPromptSubmit | When your message asks for a breakdown, comparison, ranking or a "which should I" decision over data, points Claude at `report-pages` so the answer comes back as a page. Quiet for one-number questions, code tasks and short replies. Never blocks, never echoes your words; `GNOMONKIT_PAGE_NUDGE=off` turns it off. |
| `compact_carryover.py` | PreCompact, SessionStart (compact) | Before a compaction, reads the transcript since the last one and writes a carry-over record to `~/.gnomonkit/carryover/<session>.json`: PR and issue numbers, background tasks and subagents still running, files edited, the last user message, unanswered questions, worktrees and the handoff file. After the compaction it injects that record (capped at 3,500 characters) and tells the agent to verify each item before relying on it. Deterministic, no model call, never blocks compaction; `GNOMONKIT_CARRYOVER=off` turns it off. |
| `delegation_check.py` | Agent, Task (before) | The first time a session spawns a subagent, injects the short delegation checklist (`token-efficiency` §5.3). Suggests the read-only Explore agent when a general-purpose brief reads like a search or lookup, and the cheaper `collector` agent (or a `model` parameter) when an untyped spawn with no model reads like a small read-and-return job. Never blocks, never edits the call. |
| `correction_watch.py` | UserPromptSubmit | Notices when your message reads like a correction ("you didn't…", "I had to tell you again", "make sure you…") and points Claude at `learned-triggers`. Never blocks, never echoes your words, no network. |
| `lessons_inject.py` | SessionStart (not resume), UserPromptSubmit, Write, Edit, MultiEdit, NotebookEdit (before) | Hands Claude lessons from your register ([docs/HISTORY.md](../../docs/HISTORY.md)): the always-load few at session start (pinned, then lessons from two or more sessions, at most 8), lessons matched to each prompt (at least two distinctive shared words, other repositories' never, none twice a session), and before a file's first edit the lessons that name it. Each is tagged `[L<n> · sessions · scope]` and Claude is asked which it used or set aside. Logs ids only. Never blocks. `memory.lessons` (or `GNOMONKIT_LESSONS`) is auto, on or off; auto, the default, is off where MemPalace is set up. |
| `usage_allocation.py` | UserPromptSubmit | Holds each project to its share of the weekly usage budget (`gnomonkit usage`; [design](../../docs/specs/2026-10-29-usage-allocation-design.md)). For a prompt in a project with a share, counts the project's use this UTC ISO week from the transcripts (each API message once, subagents with their parent, priced as the effectiveness report prices them; cached in `~/.gnomonkit/usage-allocation.cache.json`, so only new lines are read, at most every 30 seconds and for 2 seconds at a time). From 80% of the share it tells you and Claude, once a session; at 100% it blocks the prompt, with the use, the share and the lift command, until the week ends or you run `gnomonkit usage lift`. A project with no share, no allocation file, or any error in counting: nothing, never a block. |
| `history_refresh.py` | SessionStart | When the last history index run is over 15 minutes old and none is running, starts `gnomonkit history index` as a hidden background process and returns at once. Prints nothing. `memory.history` (or `GNOMONKIT_HISTORY`) is auto, on or off; auto, the default, is off where MemPalace is set up. |

For sandboxed runs (such as Gnomonkit Bench): `GNOMONKIT_ROUTER_STATE_DIR` moves the router's state
out of `~/.claude/skill-router`, and `GNOMONKIT_MEMORY_GLOB` sets which memory files the interlock reads
(empty = none).

Only the interlock, the heredoc guard, the grants guard, the sync guard and the usage allocation (a prompt
in a project past its share) ever block. Edit `triggers.yaml` / `skill_map.yaml`, then run
`python build_triggers_json.py` and `python build_skill_map_json.py` in `hooks/` to refresh the JSON
copies the hooks read on machines without PyYAML (CI checks they are in sync).

## The `gnomonkit` command

`gnomonkit.py` in this folder (standard library only). Run it as `python "<core>/gnomonkit.py" <command>`,
or as plain `gnomonkit` once `install-launcher` has put the launcher on your PATH. Each command lives in
its own module under `gnomonkit_cmds/`.

| Command | What it does |
|---|---|
| `profile show [--json]` | Every setting with its value, its default, where the value comes from (plugin option, environment, profile, default) and its choices |
| `profile set key=value ...` | Checks every value against the schema, keeps `personal.toml.bak`, then writes all of them; on a bad value it prints the valid choices, exits 2 and writes nothing. `machine.shell` and `machine.kind` go to the machine profile and `plan.*` to the subscription file |
| `profile unset key ...` | Back to the default |
| `setup status` / `setup done` / `setup skipped` | First-run setup state (`~/.gnomonkit/onboarding.json`), profile, disclaimer, the machine's kind and memory backend (MemPalace is suggested on a personal machine only, never installed), launcher |
| `setup security` | The settings that grant permissions, asked in a terminal (refuses without one): the orchestration grants, then the folder-trust levels (a level per root: `trusted`, `per-project` or `never`) |
| `setup remote on\|off\|status [--project DIR ...]` | For someone who works remotely or from the phone a lot: turns Claude Code's `remoteControlAtStartup` on or off in the user settings (`CLAUDE_CONFIG_DIR` honoured, the old file kept as `settings.json.bak`), records `working.remote` in the profile, and prints the checklist that stays yours, one block per `--project`: the `grants add` that lets that project's sessions start, hand off to and stop their own, and the folder trust. Never runs those itself |
| `grants list [--json]` | The orchestration grants (`~/.gnomonkit/grants.toml`), numbered, and any problem with the file. Read-only |
| `grants check --from DIR --to DIR --action A` | Whether a session in one project may spawn, hand off to, message or stop a session in another; exit 0 allowed, 2 refused. Read-only |
| `grants add --from P --to P [--to P] [--except P] --actions a,b` / `grants remove N` | Change the grants: terminal only, after you type the word shown; keeps `grants.toml.bak` and logs the change to `~/.gnomonkit/dispatch/audit.jsonl` |
| `trust [status] [--depth N] [--json]` | Each folder-trust root with its level, and each repository under it: trusted in Claude Code, pending (`trust sync` would trust it), or not, and why. Read-only |
| `trust sync [--depth N]` | Marks trusted in Claude Code the repositories under `trusted` roots that qualify and aren't yet, after listing them with the reason and the ones it leaves alone: terminal only, after you type the word shown. Checks each one again after the word (a folder swapped for a link meanwhile, or one that no longer qualifies, is not trusted, and the exit is 1). Keeps a backup of Claude Code's file and logs each change to `~/.gnomonkit/trust/audit.jsonl` |
| `usage [show] [--json]` | The weekly usage budget, each project's share, its use this UTC ISO week and its state (ok, warn, blocked, lifted). Read-only |
| `usage budget AMOUNT usd\|tokens` / `usage allow FOLDER PERCENT\|none` / `usage lift FOLDER` | Set the weekly budget, a project's share, or let a project go past its share for the rest of the week (`~/.gnomonkit/usage-allocation.toml`): terminal only, after you type the word shown; keeps `usage-allocation.toml.bak` and logs the change to `~/.gnomonkit/usage/audit.jsonl` |
| `grants deny-rules` | Prints the `permissions.deny` lines that make Claude Code refuse edits to the grants file and session commands typed into a shell; never edits settings |
| `overlay init DIR [--name N] [--github OWNER/NAME]` | Makes a private overlay in an empty or missing folder from synthetic templates and runs `git init`; commits only when your git identity is set. Prints the commands for a private remote and runs none ([docs/OVERLAY.md](../../docs/OVERLAY.md)) |
| `overlay convert REPO [--apply]` | The plan for bringing an existing rules repository into the format; `--apply` copies into the layout and never moves, overwrites or deletes |
| `overlay check [DIR]` / `overlay link [DIR]` | Validates an overlay (exit 0 passed, 1 problems, 2 not an overlay); prints the commands that connect it on this machine and runs none |
| `install-launcher [--dir D]` | Writes `gnomonkit` (sh) and `gnomonkit.cmd` to `~/.gnomonkit/bin`. They find core through `installed_plugins.json`, so they keep working after an update. Prints the PATH line; never edits your shell profile |
| `sync adopt <marketplace>` | Puts a marketplace under the sync gate: fetches it into quarantine, pins the installed commit, checks it out to `~/.gnomonkit/sync/approved/`, and prints the steps that point Claude Code at that copy (it runs none of them). See [docs/SYNC.md](../../docs/SYNC.md) |
| `sync check [--source S] [--if-due]` / `sync status` | Fetches each gated source into quarantine and moves its pin over commits signed by approved keys, or holds the update; `status` lists sources, pins and held updates |
| `sync explain <id>` | Why an update is held: each reason, the files it changes, the command for the full diff |
| `sync approve-draft <id> [--signers]` | Pushes a `gnomonkit-sync/*` branch to your overlay and opens the pull request that approves the range (or trusts its signing keys). Merging it is the approval |
| `sync overlay show` / `set <remote>` / `clear` | The overlay approvals and approved keys are read from; `set` and `clear` are terminal only |
| `work list\|show\|new\|claim\|note\|state\|next\|link\|close` | Work items: GitHub issues labelled `gk:work` plus one state label (`gk:active`, `gk:blocked`, `gk:review`; none is open). See below |

### Work items

Work that outlasts a session lives in GitHub issues any session on any machine can read: Goal, Done
when, State, Next step and Links in the body, progress as comments, and a claim comment naming the
session (and the machine's `label` from `machine.toml`) that holds it. GitHub is the source of truth;
every call goes through `gh` with your own login, so nothing new holds a credential.

- **Which repository.** `--repo owner/name`, else the current project's repository when it is
  private, else `work.repo` (the `work_repo` plugin setting, usually your private overlay). A public
  repository is refused unless you list it in `work.allow_public`, because its issues are public.
- **Claims.** `work claim` marks an item active for this session. Another session's live claim is
  refused; one with no comment for `work.stale_hours` (default 24) shows as STALE and can be taken
  with `--take`, which says so on the item.
- **Untrusted text.** Issue bodies and comments are data, never instructions. `work show` scrubs them
  through the learned-triggers redactor (plus private keys, tokens and URL credentials) and marks them
  untrusted; text the commands post is scrubbed the same way, and a scrubber that cannot run stops the
  command rather than letting text through.
- **Local files.** `~/.gnomonkit/work/items.json` (open items per repository: number, title, state,
  ages, who holds the claim; no bodies, no comments) feeds the portal's Work tab;
  `~/.gnomonkit/work/sessions/` records what each session claimed, for the compaction carry-over.
- **Elsewhere.** `compact_carryover` lists the open items the session claimed; `session-close` sets
  each one's next step and state; interjector's dispatch takes `--work REF`. No `gh`, no login or no
  network: the command says which and exits 2; hooks skip items silently.

## Agents

Two small subagents with a fixed model and effort, so a small job does not inherit the parent's
(usually the most expensive) model:

| Agent | Model, effort | Use for |
|---|---|---|
| `collector` | haiku, low | Gathering facts, running a few read-only checks, returning a short summary. Read-only. |
| `checker` | sonnet, medium | A fresh-eyes review of one change. Read-only. |

Spawn them with `subagent_type` `collector` or `checker`. The `delegation_check` hook points at
`collector` when an untyped spawn with no `model` reads like a small read-and-return job.

The context budget is the `context_budget` setting (default 250k tokens; 0 turns it off), also
`GNOMONKIT_CONTEXT_BUDGET` or `context.context_budget` in the profile. `effectiveness.py --recommend`
suggests a per-project value from your own usage. Past it, `context_watch` tells the session once per
crossing to finish the step and hand off or compact; it never blocks.

## Skills

| Skill | Use it when |
|---|---|
| `history-recall` | a question about past work ("what did we decide", "last time"), or a lessons block tagged `[L<n>]` / `[E<n>]`: search the history first, quote what it found, say so when nothing came back, mark the result that helped |
| `onboarding` | first-run setup, "Gnomonkit setup", "re-run setup", "change my preferences", "what can I set": the disclaimer, then the default questions in one message, saved only through `gnomonkit profile set` |
| `overlay-setup` | "make me an overlay", "convert this repo into an overlay", checking or connecting one: drives `gnomonkit overlay init / convert / check / link`; never creates a remote or pushes |
| `verify-before-asserting` | about to state a fact about a system, or ask the user something a search could answer |
| `before-you-build` | about to build something that may already exist or be half-shipped |
| `diff-review` | reviewing a change before it ships |
| `merged-is-not-shipped` | a merge needs to be confirmed as actually live |
| `update-discipline` | taking any dependency, image, chart or tool update |
| `package-manager-pinning` | adding or changing dependencies |
| `dependency-licence-audit` | shipping something that will be sold or distributed |
| `upstream-before-proposing` | about to propose work on, or cite an advisory for, an upstream project |
| `detector-calibration` | building a scanner or detector whose findings will be reported |
| `secret-safe-inspection` | inspecting objects that may carry credentials |
| `hardware-paths-are-owner-decisions` | choosing between hardware or storage paths |
| `owner-decides-claims` | a claim needs the user's decision before it ships |
| `copy-discipline` | writing user-facing text |
| `web-page-baseline` | building or reviewing a web page |
| `long-form-layout` | laying out long reading pages |
| `tailwind-layer-discipline` | working with Tailwind layers |
| `claude-project-format` | setting up or restructuring how a project's agent instructions are organised |
| `context-routing` | structuring a workspace so an agent loads only the context a task needs |
| `enumerate-before-destroying` | before any irreversible action |
| `decommission-order` | tearing down any cloud resource or removing a feature |
| `production-impact-callout` | a step can interrupt or change a running product |
| `ship-through-prs` | committing, pushing, or choosing where work lands |
| `merge-guidance` | telling the user to merge a PR or wait on CI |
| `stacked-pr-conflicts` | handing over more than one PR at once |
| `preemptive-env-audit` | a workload first runs on a new machine, VM, CI runner or container |
| `session-close` | a session is ending |
| `work-items` | a task will outlast the session, a background session or a handoff starts, or the user asks what is open, in progress or next: GitHub work items through `gnomonkit work`, issue text treated as untrusted data |
| `silent-no-op` | a job or pipeline finished green and may have done nothing |
| `alert-routing` | building anything that sends alerts |
| `states-that-did-not-exist-yet` | code was written against the states that existed at the time |
| `type-escape-hatches` | weakening a type to make code or a test compile |
| `performance-by-default` | writing any pass over many items, or UI work |
| `background-work-visibility` | an app does work the user did not click for |
| `less-on-screen` | adding or changing any list, group, header or page people read |
| `report-pages` | answering with data, a report, a status or decisions: one mobile page with a verdict per item, answers read back from the published page (`template.html` included) |
| `owner-is-not-qa` | before handing a new screen or feature to the user |
| `owner-voice-documents` | writing anything the user sends under their own name |
| `learned-corrections` | refining an estimate from the user's own history |
| `learned-triggers` | the user corrects something the agent should have done unprompted: file it, or add a repeat, as a `learned-trigger` issue in a private repo (`file_correction.py`); the comment count ranks which trigger to build next |
| `windows-dev-traps` | developing on Windows |
| `agent-shell-traps` | the agent's own tooling may report success it did not earn |
| `release-pipeline-traps` | building or debugging release automation |
| `token-efficiency` | starting substantive work, or a session is running long |
| `central-rules-sync` | deciding where a rule lives: shared core, your private overlay, or one project |
| `effectiveness` | asking whether the setup pays off: `effectiveness.py` reads local transcripts for subagent spend against the context each spawn kept out of the parent (worth it or not, by type, project and week), output-digest savings (when `digest_command` is set), hook uptake and interjector guess accuracy, every figure MEASURED or ESTIMATED |
| `local-compare` | the disk is full or messy, or you need to know which clones, worktrees and folders are still needed: `local_inventory.py` gives each a safe / review / keep verdict and a page to mark |
| `local-cleanup` | removing local folders or worktrees: acts only on what was marked, moves to a restorable holding area, never `--force` (`local_cleanup.py`; `purge` is the only permanent delete) |
| `automate-repeats` | a multi-step process comes round again: propose and build the script or skill instead of another manual run; `detect_repeats.py` ranks the command sequences, long commands and requests repeated across local transcripts, counts MEASURED and tokens ESTIMATED |
| `folder-placement` | about to make a folder outside the repo: worktrees, scratch and QA data each have one home, never a drive root, and every folder is labelled (`make_scratch.py`) |

## Install

From any terminal:

```text
claude plugin marketplace add joshualytle/Gnomonkit
claude plugin install core@gnomonkit
```

In the terminal CLI, `/plugin marketplace add` and `/plugin install` do the same; the IDE extensions
and the desktop app have no `/plugin` command. To update later: `claude plugin marketplace update
gnomonkit`, `claude plugin update core@gnomonkit`, then `/reload-plugins` in each open session.
See [Getting started](../../docs/ONBOARDING.md#keeping-it-up-to-date).

Requires Python 3 on the PATH (the hooks are Python, standard library only).

### How the hooks start Python

Claude Code runs a hook's `command` string through `sh -c` on macOS and Linux, and through Git Bash
on Windows (it falls back to PowerShell only when Git Bash isn't installed; `CLAUDE_CODE_GIT_BASH_PATH`
points it at a Git Bash it can't find). A bare `python` is missing on many Linux and macOS systems,
where only `python3` exists, and a hook that can't start exits 127, which Claude Code treats as a
non-blocking error: the session goes on with no interlock and no nudges. So every hook in every
Gnomonkit `hooks.json` starts the same way:

```sh
case "$OSTYPE" in msys*|cygwin*) py=python;; *) command -v python3 >/dev/null 2>&1 && py=python3 || py=python;; esac; exec "$py" "${CLAUDE_PLUGIN_ROOT}/hooks/<hook>.py"
```

- **macOS and Linux:** `python3` when it exists (so a legacy Python 2 `python` is never picked),
  else `python`.
- **Windows (Git Bash):** `python`. Git Bash's bash sets `OSTYPE` to `msys` or `cygwin`. `python3` is
  skipped on purpose: on Windows `python3` is often the Microsoft Store stub in `WindowsApps`, which
  is on the PATH (so `command -v python3` succeeds) but only prints an install hint and fails.
- **One run, its own exit code.** The choice is made before Python starts and `exec` hands over the
  process, so a hook is never run twice and a deliberate block (exit 2) reaches Claude Code unchanged.
  A `python || python3` fallback would re-run a hook that failed or blocked.
- **No extra process.** `case`, `command -v` and `exec` are shell builtins; the line adds no fork.
- **Windows without Git Bash** runs hooks in PowerShell, where this line can't parse. Install Git for
  Windows (or set `CLAUDE_CODE_GIT_BASH_PATH`), as Claude Code's own hooks documentation advises.

`tests/test_plugin_hooks.py` fails any hook command that doesn't use this exact form.

### One process per event

A Bash call used to start five hook processes, each paying for a shell and a Python start before it
did any work. `hooks/dispatch.py` is now the one hook command for UserPromptSubmit, PreToolUse and the
unmatched SessionStart group: it takes the event from the payload and runs the hooks that apply to the
tool (the table at the top of the file) one after another in the same process.

- A block (exit 2) from any hook is a block; each blocking hook's message is passed on.
- A hook that raises or exits never stops the others or the tool call.
- JSON output merges into one object (`additionalContext` and `systemMessage` joined).
- The hooks stay standalone scripts with their own tests; the dispatcher only calls their `main()`.
- Add a hook by adding it to the `HOOKS` table; the `hooks.json` matcher is the union of the table's
  tool patterns, and `hooks/test_dispatch.py` fails if the two drift apart.

`python scripts/bench_hooks.py` times one synthetic Bash call both ways on your machine.
