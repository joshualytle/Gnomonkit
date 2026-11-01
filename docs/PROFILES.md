# Profiles: one for you, one per machine

Gnomonkit's skills are written for everyone. What makes them fit *you* is a profile, read at the
start of every session, so you never have to repeat "I'm on Windows" or "give me numbered decisions".

There are two layers, because some things follow the person and some belong to the machine.

| Layer | Holds | Lives | Synced |
|---|---|---|---|
| **Personal profile** | how you like to work: how decisions are presented, reply length, when to ask first, PRs and tests, what always needs your confirmation | `~/.gnomonkit/personal.toml`, or a file in your private overlay | only if you keep it in an overlay |
| **Machine profile** | what this machine is: OS, the shell commands should be written for, GPU, and whether it is a personal or a work machine | `~/.gnomonkit/machine.toml` | **never**, created once per machine |

## No overlay needed

First-run setup (see [Getting started](ONBOARDING.md#first-run-setup)) writes your answers to
`~/.gnomonkit/personal.toml`. That is all a personal profile needs. If you keep a private overlay
repo, point the core plugin's `personal_profile` setting at a file in it (for example
`profile/personal.toml`) and the same profile follows you to every machine. [OVERLAY.md](OVERLAY.md)
explains how to make one; `gnomonkit overlay link` prints the setting for it.

The personal profile is read from the first of these that is set:

1. the core plugin's `personal_profile` setting;
2. the `GNOMONKIT_PERSONAL_PROFILE` environment variable;
3. `~/.gnomonkit/personal.toml` (`GNOMONKIT_HOME` moves the `~/.gnomonkit` folder).

## Seeing and changing it

Ask Claude ("change my preferences", "what can I set") or run the `gnomonkit` command yourself:

```text
gnomonkit profile show                  # every setting: value, default, where it comes from, choices
gnomonkit profile set communication.reply_length=short
gnomonkit profile set communication.decisions=prose delivery.prs=direct-ok
gnomonkit profile unset delivery.prs    # back to the default
```

Without the launcher on your PATH, run `python "<core>/gnomonkit.py"` instead of `gnomonkit`, where
`<core>` is the core plugin's install folder (Claude prints the full command when it needs one).

`profile set` checks every value first. On a bad value it prints the valid choices, exits 2 and
writes nothing. Otherwise it saves the old file as `personal.toml.bak` and writes the new one in a
fixed layout: tables in schema order, then any keys Gnomonkit doesn't know (kept, never dropped).
Comments are not kept; the `.bak` holds the previous version. `machine.shell` and `machine.kind`
are written to the machine profile and `plan.label` / `plan.monthly_usd` to the subscription file the effectiveness
collector reads (`subscription_file` setting, default `~/.gnomonkit/subscription.json`), as this
month's entry. `--file PATH` works on another profile file.

## Precedence

What you say in the session > machine profile > personal profile > Gnomonkit defaults.

So if your personal profile says `shell = "powershell"` but you're on your Linux laptop today, the
laptop's machine profile (`shell = "bash"`) wins there, and only there.

`machine.kind` lives only in the machine profile, never in the personal one: one person can have a
personal machine and a work machine, and a personal profile synced from one must not decide it for
the other. A `kind` in the personal profile is ignored.

Five settings are also plugin settings (`claude plugin configure core@gnomonkit`). For those, the
plugin setting wins, then its `GNOMONKIT_` environment variable, then the profile, then the default:

| Plugin setting | Environment | Profile key |
|---|---|---|
| `context_budget` | `GNOMONKIT_CONTEXT_BUDGET` | `context.context_budget` |
| `max_compactions` | `GNOMONKIT_MAX_COMPACTIONS` | `context.max_compactions` |
| `manual_compactions` | `GNOMONKIT_MANUAL_COMPACTIONS` | `context.manual_compactions` |
| `scratch_root` | `GNOMONKIT_SCRATCH_ROOT` | `paths.scratch_root` |
| `corrections_repo` | `GNOMONKIT_CORRECTIONS_REPO` | `corrections.repo` |
| `work_repo` | `GNOMONKIT_WORK_REPO` | `work.repo` |

`profile show` names the source of each value, so a profile value that a plugin setting overrides
is easy to spot.

## The first session on a new machine

1. The session-start hook finds no machine profile.
2. It **detects** what it can: operating system, which shells exist, GPU.
3. It **asks only what it can't detect**, once, in one message. For example: *"This Windows machine
   has PowerShell and Git Bash. Which should commands be written for? And is this a personal or a
   work machine?"* "Rather not say" is saved as `unknown`, which counts as a work machine and stops
   the question coming back.
4. It saves the answers to `~/.gnomonkit/machine.toml`. That file never leaves the machine.

So a second machine is asked too, even when its personal profile came from the first. A machine
profile saved before the kind existed gets that one question at the next session start. Sessions
with `GNOMONKIT_NO_ONBOARDING` set, which nobody may be watching, get no machine questions.

## Every session after that

Both profiles load silently and are injected as a short fact block, one line per value you set.
Each session also re-detects the cheap facts (OS, shell present). If they no longer match the saved
file, the agent **asks before updating** rather than working from stale settings. A personal profile
that can't be read is noted in one line and the defaults apply; `gnomonkit profile show` says what is
wrong with it.

## The memory backend

What Gnomonkit points you at for memory depends on the machine's kind.

- **Personal machine:** when MemPalace isn't set up, first-run setup and `gnomonkit setup status`
  recommend [MemPalace](https://github.com/MemPalace/mempalace), an open-source memory (MIT licence)
  that keeps past conversations as verbatim text and finds them again with semantic search, through
  an MCP server. Its README has the install and verify steps. Its README says nothing leaves the
  machine unless you opt in; the opt-ins it lists are a remote embedding endpoint, a storage backend
  on a server, a shared hub other machines connect to, and release checks that contact PyPI. It also
  downloads its embedding model the first time it needs one. **Gnomonkit never installs it.**
- **Work machine, or a kind that is unknown or not set:** the built-in local history index is the
  default, and MemPalace is not mentioned.

MemPalace counts as *installed* when a `mempalace` executable is on PATH, and as *set up* when Claude
Code has an MCP server whose name or command line contains "mempalace": in `.claude.json`
(`CLAUDE_CONFIG_DIR`, else `~/.claude.json`; the top level and every project), in a `.mcp.json` in
the folder or at its repository root, or from an installed plugin. Hooks are not looked at. The
answer is kept for an hour in `~/.gnomonkit/memory-backend.json`.

With MemPalace installed or set up, Gnomonkit's own history index and lessons stay off unless you
turn them on: `memory.history` and `memory.lessons` default to `auto`, which is off on such a
machine and on anywhere else, whatever its kind. `on` or `off` in the profile, or
`GNOMONKIT_HISTORY` / `GNOMONKIT_LESSONS` in the environment, overrides it
([HISTORY.md](HISTORY.md#on-off-or-auto)).

## Cloud and web sessions

They can't see your machine. The hook uses what it detects in that environment and skips the
machine file. First-run setup is never offered there.

## Other agents (Codex, Gemini CLI, Cursor…)

They have no session-start hooks, so for now the profile reaches only Claude Code.

## Settings

The default set is what first-run setup asks, in one message; advanced is offered after it.

| Key | Set | Choices | Default |
|---|---|---|---|
| `communication.decisions` | default | numbered, prose | numbered |
| `communication.reply_length` | default | short, normal, detailed | normal |
| `working.ask_before_acting` | default | always, risky, rarely | risky |
| `working.remote` | default | yes, no | no |
| `context.context_budget` | default | tokens, 0 = off | 250000 |
| `context.max_compactions` | default | automatic compactions, 0 = off | 2 |
| `context.manual_compactions` | default | your own /compact runs allowed after that | 1 |
| `machine.shell` | default | the shells found on this machine (asked only when there are several) | the only one found |
| `machine.kind` | default | personal, work, unknown (rather not say; counts as work) | not set: asked once per machine |
| `delivery.commit_email` | default | noreply, any | noreply |
| `delivery.prs` | default | always, direct-ok | always |
| `delivery.test_style` | default | test-first, tests-after, match-repo | match-repo |
| `delivery.merge` | default | agent (the agent merges its own pull request once it is green), ask (it asks you first, every time), ask-for (it merges its own, except those matching `merge_ask_for`) | agent |
| `safety.always_confirm` | default | a list, items separated by commas | deleting data, force-pushes and history rewrites, publishing anything |
| `memory.lessons` | default | auto, on, off: lessons from your history at session start, per prompt and before a file's first edit ([HISTORY.md](HISTORY.md)); auto is off when MemPalace is set up here and on otherwise ([HISTORY.md](HISTORY.md#on-off-or-auto)); `GNOMONKIT_LESSONS` overrides it | auto |
| `orchestrator.mode` | default | off, claude, api, ollama: a model reads the last few messages and suggests skills (interjector plugin; see its README) | off |
| `communication.explanation_level` | advanced | brief, standard, teach | standard |
| `working.subagents` | advanced | sparing, normal, liberal | normal |
| `dispatch.primary_client` | advanced | any, terminal, vscode | any |
| `orchestrator.model` | advanced | a model name; empty uses Haiku for claude and api, and is required for ollama | empty |
| `plan.label` | advanced | text, up to 60 characters (subscription file) | not set |
| `plan.monthly_usd` | advanced | a number (subscription file) | not set |
| `delivery.ci` | advanced | self-hosted, hosted, none | not set |
| `delivery.ci_runner_label` | advanced | text, when `ci` is self-hosted | not set |
| `delivery.hosted_minutes_budget` | advanced | whole number, when `ci` is hosted | not set |
| `delivery.merge_ask_for` | advanced | a list, when `merge` is ask-for: path globs (`docs/**`, `*.lock`) a pull request's changed files are matched against, and `label:<name>` for a pull request label | none |
| `code.style` | advanced | one short line of text, up to 200 characters | not set |
| `paths.scratch_root` | advanced | a folder, not a drive root | `~/.gnomonkit/scratch` |
| `alerts.destination` | advanced | slack, email, none | not set |
| `alerts.issues_only` | advanced | true, false | false |
| `corrections.repo` | advanced | a PRIVATE GitHub repo, `owner/name` | not set |
| `memory.history` | advanced | auto, on, off: Gnomonkit's own history index (the session-start refresh and `history index`); auto is off when MemPalace is set up here and on otherwise ([HISTORY.md](HISTORY.md#on-off-or-auto)); `GNOMONKIT_HISTORY` overrides it | auto |
| `memory.exclude_projects` | advanced | a list of project names or folders the history index never reads ([HISTORY.md](HISTORY.md)) | none |
| `memory.history_keep_days` | advanced | whole number of days the history index keeps; older rows go at each index run, 0 keeps them all ([HISTORY.md](HISTORY.md#how-much-it-keeps)) | 365 |
| `memory.history_max_mb` | advanced | whole number of MB the history index may hold; past it the oldest days go, 0 is no cap ([HISTORY.md](HISTORY.md#how-much-it-keeps)) | 1024 |
| `work.repo` | advanced | a PRIVATE GitHub repo, `owner/name`, for work items that cross projects | not set |
| `work.allow_public` | advanced | a list of public repos (`owner/name`) where work items may still be kept | none |
| `work.stale_hours` | advanced | hours with no comment before a claimed work item shows as stale | 24 |
| `acknowledged.disclaimer` | recorded by setup | the disclaimer version you acknowledged | not set |

The interjector's session-dispatch also reads a `[dispatch]` table from this file: `auto_stop`
(true, false; default true), `stop_timeout_minutes` (a number above 0, at most 1440; default 15) and
`primary_client` (`"any"`, `"terminal"`, `"vscode"`; default `"any"`), `tab_seconds` (how long a VS Code
tab primary's own process is waited for after the link, 0 to 300; default 20), and `primary_model`,
`primary_effort`, `sub_model`, `sub_effort` (defaults `""`, `"medium"`, `"sonnet"`, `"medium"`). See
[session roles](../plugins/interjector/README.md#roles-one-primary-per-project-subs-that-stop-when-finished).
`profile set` manages `dispatch.primary_client`; add the rest of the table by hand (`profile set` keeps
keys it doesn't know). A `GNOMONKIT_DISPATCH_*` variable of the same name wins over the file.

Which `primary_client` to pick depends on where you are when you reply:

| `primary_client` | On the desk | On the phone |
|---|---|---|
| `terminal` | its claude.ai/code link, or `claude attach <id>` in a terminal; not the VS Code session list (a second process on the conversation exits at once and loses your message) | always, while it runs |
| `vscode` | the VS Code tab | only while VS Code has the tab open and running |
| `any` | a tab where the `code` command is found, else as `terminal` | as the one it resolved to |

`gnomonkit setup remote on` sets `terminal` (pass `--client` for another), since remote work needs a
process that outlives the desk; `setup remote status` warns when `working.remote` is yes and the
client is `vscode`.

Session titles follow a `[naming]` table in the same way: `primary` (default `"{repo}:{task}"`), `sub`
(default `"sub:{parent}/{task}"`) and `prefix` (none by default). The fields, the rules a scheme must
keep and what happens when it breaks one are in
[session names](../plugins/interjector/README.md#session-names-and-the-board). Add the table by hand;
a `GNOMONKIT_NAMING_PRIMARY`, `_SUB` or `_PREFIX` variable wins over the file.

### Usage allocation

Each project's share of your weekly usage is not in this file: an agent can edit the profile, and raising
its own project's share would lift its own limit. It lives in `~/.gnomonkit/usage-allocation.toml`
(`GNOMONKIT_HOME` moves it), which only `gnomonkit usage` writes, in a terminal, and which the core guard
hook keeps agents from editing. The design is in
[docs/specs/2026-10-29-usage-allocation-design.md](specs/2026-10-29-usage-allocation-design.md).

| Key | Set with | Choices | Default |
|---|---|---|---|
| `budget.weekly` | `gnomonkit usage budget AMOUNT UNIT` | a number, per UTC ISO week; 0 limits nothing | not set: nothing is limited |
| `budget.unit` | `gnomonkit usage budget AMOUNT UNIT` | `usd` (API-equivalent dollars, priced as the effectiveness report prices them) or `tokens` (input, output, cache write and cache read) | `usd` |
| `[[project]]` `folder`, `share` | `gnomonkit usage allow FOLDER PERCENT` (`none` removes it) | an absolute folder and a percent of the budget; shares add up to 100 at most; the longest matching folder wins | none: a project with no share is not limited |
| `[[lift]]` `folder`, `week` | `gnomonkit usage lift FOLDER` | lets the project go past its share until the week ends | none |

From 80% of its share a project's sessions get a notice once each; at 100% new prompts there are
stopped until Monday 00:00 UTC or a lift. `gnomonkit usage` shows the budget, the shares and each
project's use this week. Use is counted from the transcripts, so it is an estimate of the plan's own
meter, not a reading of it.

An example `personal.toml`, as `profile set` writes it:

```toml
# Gnomonkit personal profile. See docs/PROFILES.md.
# Written by `gnomonkit profile set`, which checks every value. Comments are not kept;
# the previous version is saved beside this file as personal.toml.bak.

[communication]
decisions = "numbered"
reply_length = "short"

[working]
ask_before_acting = "risky"

[delivery]
ci = "hosted"
hosted_minutes_budget = 2000

[safety]
always_confirm = ["deleting data", "publishing anything"]

[acknowledged]
disclaimer = "1"
```

Machine (`~/.gnomonkit/machine.toml`, written by the first-session flow or by `profile set` with
`machine.shell=...` or `machine.kind=...`):

```toml
os = "windows"                # windows | macos | linux (detected)
shell = "powershell"          # the shell commands are written for (asked if several exist)
kind = "work"                 # personal | work | unknown (asked once; unknown counts as work)
gpu = "none"                  # detected; none if absent
label = "desk"                # optional, added by hand: the name work-item claims give this machine
```

`label` is never detected (a host name is not used in its place): without it, or the
`GNOMONKIT_MACHINE_LABEL` environment variable, a work-item claim says "an unlabelled machine".
