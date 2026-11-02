# Changelog

Each entry names the plugins it changes. Versions follow semantic versioning per plugin; the
version guard in the test suite requires a bump whenever a plugin's hooks or skills change.

## core 1.9.2

- **The destructive-command interlock covers four more kinds of loss.** `git reset --hard`
  (uncommitted changes), `git clean` with force (untracked and ignored files), Docker volume removal
  (`docker volume rm` and `prune`, `system prune --volumes`, `compose down -v`), and infrastructure
  destroys (`terraform`, `tofu` or `terragrunt destroy`, `apply -destroy`). Must-allow cases keep
  the safe neighbours quiet: `reset --soft`, unstaging a file, `clean -n`, `docker volume ls`, a plain
  `compose down`, `terraform plan -destroy`.
- `ship-through-prs` no longer reads a trailing `# ...` comment as part of a push: a branch delete
  whose comment mentioned main fired it.

## core 1.9.1

- **The destructive-command interlock catches the other common spellings.** Recursive deletes with
  separate or long flags (`rm -r -f`, `rm --recursive --force`), PowerShell `Remove-Item -Recurse`
  and its abbreviations, cmd `rd /s`, `rmdir /s` and `del /s`; force pushes as `git push -f`, a
  bundled `-uf` and a `+` refspec; and `kubectl delete namespace`. Each has must-allow neighbours
  (`rm -r` alone, `git push -u`, `--force-with-lease`, `kubectl get ns`, `dir /s`).
- New `docs/THREAT-MODEL.md`: who the blocking hooks are for, how each fails, what the interlock
  covers and what it does not. `SECURITY.md` links to it.

## core 1.9.0

- **Three router triggers stop firing on the compliant action.** `ship-through-prs` fires on a push
  that names main or master, and on a commit or bare push from a checkout on main or master. It no
  longer fires on `gh pr create` or `git push -u`. `diff-review` keeps the feat/fix commit pattern
  and drops `gh pr checkout` and `gh pr diff`. `folder-placement` stays quiet for a worktree or
  clone into `.claude/worktrees/` or `<repo>.worktrees/`; the drive-root mkdir pattern is unchanged.
- **A trigger pattern can depend on the branch.** A `command_patterns` entry may be
  `{pattern, branches}`, which counts only when the working folder's checkout is on one of those
  branches. The router reads `.git/HEAD` (a linked worktree's too) and never runs git. Fixtures for
  such a rule are `{command, branch}`.
- The dispatcher test's router probe now has to fire. It used a command no rule matched, so the
  comparison passed without comparing anything.

## core 1.8.1

- **A router can name itself on a suggestion line.** A router whose rules point at other plugins'
  skills prints the skill's own name followed by `(router: <plugin>)`, so the Skill call stays right
  and the effectiveness report still knows whose rule fired. The mark wins over the name's prefix in
  `by`. Core's router adds it when a rule names another plugin's skill; a private overlay's copy of
  the router should do the same. Rescans the cache once (SCAN_VERSION 12).

## core 1.8.0, portal 1.0.1

- **Skill uptake leaves out suggestions of a skill already loaded.** Loading it again changes nothing,
  so those suggestions counted against the rate without measuring anything. They stay in the counts as
  `already_loaded`; uptake is now taken over `open` (suggested minus already loaded).
- **Each router row names its source.** `by` counts the suggestions per plugin prefix from the
  suggestion line, so core's rules and a private overlay's rules no longer mix in one row. The portal
  names each skill by its main source.
- **Follow-through.** A trigger rule can list `follow_through` shell patterns (with `follow` fixtures
  the router test checks). The report counts a suggestion as `followed` when a matching command runs
  within the uptake window, so advice acted on without loading the skill shows. Patterns come from
  core's rules, `GNOMONKIT_TRIGGER_FILES`, and each installed plugin's `hooks/triggers.json`. Core's
  ship-through-prs and merged-is-not-shipped rules carry the first patterns.
- **Self-invoked skill calls.** `hooks.skills_self` counts Skill calls that no suggestion led to.
- The scan format changed (SCAN_VERSION 11), so cached transcripts are rescanned once.

## interjector 1.6.9

- **Comment only.** `open_uri`'s docstring gives the reason for `code --open-url` by its mechanism.

## interjector 1.6.8

- **The extension's link is sent with `code --open-url`.** The system's protocol handler, launched from a
  session's shell, can fail to reach the running VS Code window and says nothing, so a dispatched tab
  never opened while the report said it had. Wherever the `code` command is found the link now goes
  through it; the system handler is the fallback, and `GNOMONKIT_URI_OPENER` still overrides both.

## interjector 1.6.7

- **A dispatched VS Code tab is called open only once its process exists.** VS Code hands an
  extension a link only after the user has allowed that extension once (a dialog it drops unanswered
  after five minutes, or the setting `extensions.confirmedUriHandlerExtensionIds`), which nobody sees
  from a phone. Dispatch now reads that before sending the link, waits `tab_seconds` (default 20)
  for the tab's own process afterwards, and when the tab did not open says so and names what to
  allow, instead of reporting a tab that is open nowhere.

## core 1.7.5, interjector 1.6.6

- **Remote work gets a primary the phone always sees.** `gnomonkit setup remote on` now also sets
  `dispatch.primary_client = "terminal"` (pass `--client` for another choice) and says why: a VS Code
  tab is on the phone only while VS Code has it open and running, while a background primary with
  Remote Control always is, joined at the desk from its claude.ai/code link or `claude attach`, never
  the VS Code session list (a second process on the conversation exits and loses the message).
  `setup status` and `setup remote status` show the client and warn when `working.remote` is yes
  with a `vscode` client; the onboarding skill, PROFILES.md and ONBOARDING.md carry the trade-off
  table; and dispatch's start report says it when a tab opens for someone who works remotely.

## core 1.7.4

- **Setup asks about remote and phone work.** A new default-set question, `working.remote`. On yes,
  `gnomonkit setup remote on` turns Claude Code's `remoteControlAtStartup` on in the user settings
  (the old file kept as `.bak`), records the answer in the profile, and prints one checklist per
  project (`--project DIR`) of what stays yours: the grant that lets that project's sessions start,
  hand off to and stop their own, and the folder trust. `setup status` shows the setting. The
  session-start block carries the answer, and `dispatch.primary_client` is now a validated profile
  key (advanced), so it can be set with `profile set`.

## interjector 1.6.4

- **A naming-scheme edit is seen even inside one clock tick.** The scheme cache is keyed on the
  profile's modification time and size, not the time alone, so two edits that land in the same
  filesystem timestamp tick no longer serve the first one.

## interjector 1.6.3

- **A primary with Remote Control stays running.** A stopped session is reachable nowhere until it is
  opened, while a running one is on the phone, at its claude.ai/code link and under `claude attach`.
  So with `primary_client = "any"` (the default) dispatch no longer parks a primary it started with
  `--remote-control`; its brief, the start report and `list` name those routes. A VS Code tab cannot
  take over a running background session (the take-over exits and a message typed there is lost), so
  none of them point to the tab. Without Remote Control the watcher parks it as before.
- **A resumed session keeps its title.** `--name` is passed on a resume too, so `claude agents` shows
  `<repo>:main-v<N>` instead of a made-up title.

## core 1.7.1

- **Automatic and manual compactions are counted apart.** `max_compactions` now counts automatic
  compactions only. Your own `/compact` runs count against a new setting, `manual_compactions`
  (default 1). When the automatic cap is reached, Claude writes the handoff and tells you that you can
  still `/compact` or start fresh. A fresh session is recommended once the manual allowance is spent.

## interjector 1.6.0, core 1.7.0

- **Orchestrator (interjector, off by default).** After each prompt, a model reads the last three
  prompts and the replies between them, redacted, and names the situation from core's skill map.
  Claude gets that situation's skills as a comment at its next tool call. The model is `claude -p`
  with your login, the Anthropic API, or a local Ollama server. Its answer must be one of the map's
  situation names, and the comment is written from the map, never from the model's words. It runs
  in a detached process, so a prompt never waits. A suggestion left over from the turn before is
  dropped, and the same situation is suggested at most once every 20 minutes. `orchestrator_eval.py`
  scores a model on labelled, synthetic conversations.
- **Setup asks about it (core).** `orchestrator.mode` (off, claude, api, ollama) is in the default
  set of first-run questions, and `orchestrator.model` is in the advanced set. The onboarding skill
  says what each choice costs and what leaves the machine.

## interjector 1.5.0

- **Reply check.** The skill routers see prompts and tool calls, so a final reply that is only text
  passed unseen, and a report or a list of decisions reached the user as terminal text instead of a
  page. At Stop, a reply of 30 or more lines, or a shorter one with a decisions list of three or
  more items, in a turn that published no page, is now held once with a note pointing at
  core:report-pages. Replies that are mostly a fenced block are left alone. Plain rules, no model;
  `GNOMONKIT_REPLY_CHECK=off` turns it off.

## core 1.6.2, supply-chain 1.0.2

- **More release-review wording.** The destructive interlock's examples of what curated memory
  holds, the learned-corrections skill's example spread, and supply-chain's router comment are now
  plainly generic. No behaviour changes.

## core 1.6.1

- **Release-review wording.** The destructive interlock, the skill router and the agent-shell-traps
  skill described their reasoning with examples that read as one setup's own incidents and
  measurements. They now state the general pattern only. No behaviour changes.
- **Tests run from the public export.** The disclaimer-manifest test and the manual trust-check test
  skip when the development-only files they read are not present.

## core 1.6.0, interjector 1.4.0

- **More background sessions, counted fairly.** The dispatch cap now defaults to 8 and
  `max_sessions = 0` means no cap (it used to refuse every spawn); a negative or non-numeric value
  falls back to the default. It counts only the running background sessions dispatch
  started. Your own sessions, the primary doing the dispatching, and primaries you have opened in a
  tab no longer take a slot.
- **The grants guard no longer misreads redirections.** `claude rm --help 2>&1` was refused as a
  stop of a session named `2`. The shell scanner now reads the `2` in `2>&1` as a file descriptor,
  the guard ignores redirection targets after `claude`, and `--help`, `-h` and `--version` act on
  nothing.
- **You choose who merges.** The new `delivery.merge` setting is `agent` (the default: the agent
  merges its own green PRs), `ask`, or `ask-for` with a `delivery.merge_ask_for` list of globs or
  labels. The profile shows it as a "Merging" line. The ship-through-prs and merge-guidance skills
  and every dispatch brief follow it.
- **Open sessions are told when plugins update.** `plugin_freshness` records the plugin versions
  each session loaded. When a newer version installs, it tells the session once, at its next
  prompt, to run `/reload-plugins`. It doesn't repeat while that notice is unanswered, and speaks
  again only after you have used the session and a newer update has landed.
- **Grants fit how dispatch is used.** The spawn grant now also lets a session message and stop the
  subs it started in the same project, and lets a project hand off to itself. A worktree of a project
  (`<project>/.claude/worktrees/...`, `<project>.worktrees/...` or any `git worktree add` path)
  counts as the project. Acting across projects and stopping sessions you didn't start still need
  their own grants. A missing grant is named once per session, with the full
  `python "<core>/gnomonkit.py" grants add ...` command. Dispatch refuses an untrusted target folder
  up front, before it writes anything.
- **An opened primary is a tab.** If you open a background primary in a tab, it is recorded as
  one: it takes no cap slot, a handoff retires it without a stop grant, and stop-when-idle leaves it
  running. `list` and the "how to open it" line say where the session actually is.
- **Dispatched sessions stay out of EnterWorktree.** A new PreToolUse hook refuses EnterWorktree in a
  session dispatch started, because it moves the session's folder away from its project. The
  refusal gives the `git -C <project> worktree add <project>/.claude/worktrees/<name> -b <branch>
  origin/<default>` pattern and asks for absolute paths. Design:
  `docs/specs/2026-10-29-dispatch-fixes-design.md`.

## core 1.5.0

- **One hook process per event, not one per hook.** A shell call used to start five hook
  processes (and a prompt five more), each paying for a shell and a Python start. `hooks/dispatch.py`
  now runs the applicable hooks in turn in one process for UserPromptSubmit, PreToolUse and the
  unmatched SessionStart group, with the same decisions: a block from any hook blocks, one hook
  failing never stops the others, and their context output is merged. A block is written out at once
  and the guards run first, so a slow advisory hook cannot time a block out. The hooks remain standalone
  scripts. `scripts/bench_hooks.py` times a synthetic call both ways so you can measure it yourself.
- **Every core skill now shows its description.** The long descriptions overflowed the skill
  listing's size budget, so most skills appeared without one and the agent could not tell when they
  applied. Each description is now one short "Activates when..." line; where the old text said more,
  it moved into the skill's body.

## core 1.4.0

- **The effectiveness report shows what each spawn ran on and how it did.** Subagent rows carry the
  `model` they ran on (and `effort` when recorded), grouped by type and by model, with a `cheap_types`
  line for `collector` and `checker`. Three accuracy signals per group, each a rate with its sample
  size and n/a when nothing was measurable: `redone` (the parent repeated the subagent's reads or
  commands), `corrected` (a user correction in the next two prompts), `rejected` (the parent called the
  result wrong). They are proxies, labelled SIGNAL, and the report says what each one misses.
- **Dispatched sessions are in the report.** Sessions started by session-dispatch are read from its
  record file as data (core imports no interjector code): role, model, pull requests recorded and
  merged, user corrections per prompt. CI before merge is not recorded, so it is not reported.

## core 1.3.0

- **Each project can have a share of your weekly usage.** `gnomonkit usage budget 400 usd` sets a
  weekly budget (API-equivalent dollars or tokens) and `gnomonkit usage allow <folder> 70` gives a
  project 70% of it. A new UserPromptSubmit hook counts each project's use this UTC ISO week from the
  transcripts, as the effectiveness report counts usage, and keeps a cache so a prompt reads only new
  lines. From 80% of the share it tells you once a session; at 100% it stops new prompts in that
  project until the week ends or you run `gnomonkit usage lift <folder>`. Projects with no share are
  not limited, and an error in counting never blocks. The allocation lives in
  `~/.gnomonkit/usage-allocation.toml`, never in a project; only you change it, at a terminal, and
  the grants guard keeps agents from editing it or its cache. `gnomonkit usage` shows the shares and
  each project's use. Design: `docs/specs/2026-10-29-usage-allocation-design.md`.

## core 1.2.0

- **Cheaper subagents.** New `collector` (haiku, low effort) and `checker` (sonnet, medium) agents
  for small read-and-return jobs and one-change reviews. `delegation_check` now points an untyped
  spawn that sets no `model` and reads like a small job at `collector` or the `model` parameter.
  It still never blocks.
- **The context budget acts.** Past `context_budget`, `context_watch` tells the session to finish
  the current step, write a handoff brief and start a fresh session from it or compact, once per
  crossing, with the numbers.

## core 1.1.0, supply-chain 1.0.1

- **The skill router no longer approves the tool call it comments on.** Its PreToolUse output
  carried `permissionDecision: "allow"`, which skips the permission prompt, so a Write, Edit or
  shell call that matched a trigger ran without asking. The suggestion is now context only.
- **A suggestion asks for the load.** It names the skill as `plugin:skill` and asks for the
  Skill tool call (skipped when the skill is already loaded), instead of asking the agent to
  "evaluate against" a skill it could only judge by name.

## interjector 1.3.0

- **A dispatched session starts on a model and effort that fit its role.** It used to inherit the
  user's default model at the user's default effort, however small its task. New `[dispatch]`
  settings `primary_model` / `primary_effort` (defaults: your own model, `medium`) and `sub_model` /
  `sub_effort` (`sonnet`, `medium`), and `--model` / `--effort` on `spawn` and `handoff` for one call.
  The session-dispatch skill now says which to pick for which kind of task.

## interjector 1.2.0

- **A primary stays where you can open it.** Its brief now tells it not to run EnterWorktree, which
  moves the conversation into the worktree's folder, where the project's session list no longer shows
  it. A primary that is parked once its first turn ends is told to keep that turn to a short report,
  since a long one outlasts the watcher that parks it.
- **A VS Code tab, when you ask for one.** `--client vscode` (or `primary_client = "vscode"`) opens
  a primary as a tab in the project's window (`code <project>`, then the extension's open link) with
  the pointer to its brief typed in. VS Code never sends that prompt, so you press Enter; the default
  stays the background. The naming hook titles the tab from its brief.
- **Handing off from a tab stops nothing.** `handoff --retire` on a primary that is a tab records it
  as retired instead of stopping it, needs no stop grant, and tells you to close the tab.
- The session-dispatch skill now covers numbered primaries (`<repo>:main-v<N>`), not announcing a
  primary until it is parked and listed, and accounting for every older session on a handoff.

## interjector 1.1.0

- **Sessions dispatch starts can be opened without a stop grant.** Dispatch parks (`claude stop`,
  which keeps the conversation) a primary it started once its first turn ends, and a sub of its own
  once it has finished. The spawn or handoff grant into that project now allows this as well as the
  stop grant, so a primary is no longer left running in the background where the IDE cannot open it.
  Stopping any other session (`stop`, `--retire`, `--replace`) still needs the stop grant.

## 1.0.0

The first public release. Every plugin (core, interjector, portal, supply-chain) moves to 1.0.0.
Compared with the last pre-release versions:

- **Disclaimers.** `DISCLAIMER.md` states what the toolkit does and does not protect against, the
  README carries the status line and a link to it, and `SECURITY.md` names a reply time.
- **First-run onboarding.** The `gnomonkit` command: `setup` asks once per machine whether this is
  a personal or a work machine and writes a profile, so the plugins work without an overlay.
- **Full function without MemPalace.** A local history index over the user's own transcripts,
  with lessons and precedent drawn from it, used by the interjector's history search and the
  portal's Memory tab when no memory backend is installed. The backend setting defaults to `auto`.
- **Overlay creation.** `gnomonkit overlay` creates a personal overlay repository, or converts an
  existing folder into one, with the settings the plugins read from it.
- **Orchestration grants.** A user-only grants file decides what one session may do to another
  (start, stop, resume in another repository); a guard keeps agents from editing it, and every
  decision is written to an audit log.
- **Folder trust levels.** Trust is set per root folder, new repositories under a trusted root are
  trusted automatically, and `gnomonkit trust` shows and changes the levels.
- **Portal hardening and snapshots.** The portal binds to loopback by default, validates every
  request body and host, and can write a static snapshot of its pages.
- **Sync gate.** Pinned sources, fetch into quarantine, signed auto-advance, held updates, and
  approval through a pull request in the overlay.
- **Work items on GitHub.** `gnomonkit work` keeps work items as GitHub issues, and the dispatch
  command can start a session from one.
- **History from GitHub.** `gnomonkit history index --github [owner/name]` indexes work items and
  their comments into the local history, scrubbed and linked, so `history search` finds them beside
  past sessions. Each run reads only what changed since the last one.
- **Session roles.** One primary session per project, one session per name, labelled subsessions
  that stop when their work is finished, and empty tabs hidden in the portal.
- **Session naming scheme.** One primary and one sub template name every session. The naming hook,
  dispatch and the roles module share them. The user can set them in a `[naming]` table in the
  personal profile, or in environment variables. `dispatch.py naming` shows the scheme in effect
  and any problems. The defaults are the names used before.
- **Portal Trust tab.** A read-only tab showing each trust root and its level, how each repository
  under it resolves, and the folders Claude Code trusts that no root covers. It is served on
  loopback only and comes from `gnomonkit trust status --json`.
- **Clearer effectiveness figures.** The first and last weeks of the window are marked as part
  weeks with their days. Each group shows the spend of its linked subagents beside the reads they
  avoided. Skill uptake counts per suggestion, the same as the router's uptake. The daily usage
  rows say they are UTC days.

Known limits are listed in `DISCLAIMER.md`. Grants are a policy layer, not a sandbox: they decide
what the tooling will do on a session's behalf, not what a process on the machine can do.
