# Getting started with Gnomonkit

This walks you from nothing to a working setup: the core plugin, keeping it current, the optional
plugins, your profiles, and a private overlay of your own rules.

Before you start: Gnomonkit is maintained by one person and provided as is, without warranty and
without liability for what installing or running it causes. Its hooks run code in every session,
and sessions it starts act with your account. Read the [disclaimer](../DISCLAIMER.md) first, and on
a work machine check your employer's policies on AI tools and plugins.

## 1. Install the core

From any terminal (works whichever Claude Code you use: terminal, VS Code, JetBrains or desktop):

```text
claude plugin marketplace add joshualytle/Gnomonkit
claude plugin install core@gnomonkit
```

In the terminal CLI you can use the slash commands instead: `/plugin marketplace add
joshualytle/Gnomonkit`, then `/plugin install core@gnomonkit`. The `/plugin` command exists only in
the terminal CLI; the IDE extensions and the desktop app answer "/plugin isn't available in this
environment".

### Before Claude starts sessions for you: two separate questions

The interjector's session dispatch lets a Claude session start, hand off to, message or stop other
sessions. Two different permissions decide whether it may, and you answer each yourself:

1. **Orchestration grants (Gnomonkit): may a session in one project act in another?** Default deny:
   until you grant it, every such request from a session is refused. Set them in a terminal with
   `gnomonkit setup security` (see [Orchestration grants](#orchestration-grants) below).
2. **Folder trust (Claude Code): may Claude run in that folder at all?** Claude Code asks the first
   time you run `claude` in a folder **from a terminal**, and `claude --bg` refuses a folder that was
   never trusted there. The IDE extensions and the desktop app don't record that answer, so a folder
   you work in every day can still be untrusted. Trust is per repository: Claude Code keys it on the
   git repository root, and trusting the folder above a repository covers plain subfolders, not the
   repositories inside it. Two ways to answer:

   - **By level, per root (Gnomonkit).** In `gnomonkit setup security`, give each folder that holds
     projects a level: `trusted`, `per-project` or `never` (see
     [Folder-trust levels](#folder-trust-levels) below). Under a `trusted` root, `gnomonkit trust
     sync` (in a terminal) trusts the repositories there whose owner you list, and with `auto` on,
     new clones are trusted when a session starts, so you don't run a command for each one.
   - **By hand, per repository (Claude Code).** In a terminal:

     ```text
     cd <projects folder>/<project>
     claude
     ```

     Accept the trust prompt, then type `/exit`.

   Only trust projects whose code you know: a trusted folder's own Claude settings, including its
   hooks, run without asking. Before Claude starts a session somewhere it does a dry run, which shows
   the folder's trust level, the grant decision and whether that folder is trusted.

A grant never trusts a folder, and trusting a folder grants nothing: a session needs both.

### Keeping it up to date

When a session says *Plugin update pending*, run in a terminal:

```text
claude plugin marketplace update gnomonkit
claude plugin update core@gnomonkit
```

(repeat the last line for each Gnomonkit plugin you use), then type `/reload-plugins` in each open
session. `/reload-plugins` works in every client. A few things load only when a session starts,
such as the skill map; restart the session for those.

To take updates only when they are signed by a key you approved, and review anything else before it
runs, put the marketplace under the sync gate: `gnomonkit sync adopt gnomonkit`. See
[SYNC.md](SYNC.md). A gated update that is held says *update held for review* instead, with no
install commands.

### Settings

Every setting is optional; the plugins work with none. To see a plugin's settings and which are
unset, run `claude plugin configure core@gnomonkit` in a terminal. To change them, start `claude` in
a terminal and type `/plugin configure core@gnomonkit`; the settings are saved for every client, so
they apply in your IDE too after `/reload-plugins`. (The IDE extensions and the desktop app have no
`/plugin` command, which is why installing tells you to use it in Claude Code's terminal.)

From a POSIX shell such as Git Bash you can also pipe values in:

```sh
echo '{"context_budget": "300000"}' | claude plugin configure core@gnomonkit --values-stdin
```

Requires Python 3 on the PATH, as `python3` or `python`. The hooks use only the standard library. On
Windows, hooks run in Git Bash: install Git for Windows if it isn't there. See
[how the hooks start Python](../plugins/core/README.md#how-the-hooks-start-python).

### First-run setup

The first session you start after installing, while you have no personal profile, Claude offers a
one-time setup after answering whatever you asked: about two minutes, every question has a default,
and you can skip it. It is offered at most twice (two session starts), only when a session starts
fresh (not on resume or after a compaction), and never in a session nobody is watching: sessions the
interjector's session dispatch starts carry `GNOMONKIT_NO_ONBOARDING=1`, and remote and web sessions
skip it too. Set `GNOMONKIT_NO_ONBOARDING=1` yourself to turn the offer off. Once it has been made
twice, the session-start profile carries one line instead: ask for "Gnomonkit setup" any time.

The setup:

1. Shows the [disclaimer](../DISCLAIMER.md) as one short paragraph and asks you to acknowledge it or
   skip. Until you acknowledge it, each session start carries one line pointing at it.
2. Asks the default questions in one message, each with its default shown (how decisions are laid
   out, reply length, when to ask before acting, context budget, PRs, tests, what always needs your
   confirmation, and a few more). Answer by number, say "defaults", or "skip". Say "advanced" for the
   rest. [PROFILES.md](PROFILES.md#settings) lists every setting.
3. Saves your answers to `~/.gnomonkit/personal.toml` through the `gnomonkit` command, which checks
   every value and keeps the previous file as `personal.toml.bak`. Claude never edits the file
   itself. No overlay is needed.
4. Suggests a memory backend. On a personal machine where [MemPalace](https://github.com/MemPalace/mempalace)
   isn't set up, it recommends it: what it is, its licence (MIT), that its README has the install and
   verify steps, what can leave the machine (by its README, nothing unless you opt in: a remote
   embedding endpoint, a storage backend on a server, a shared hub, release checks against PyPI; it
   also downloads its embedding model on first use). Gnomonkit never installs it. Where MemPalace is
   set up, Gnomonkit's own history index and lessons stay off unless you turn them on
   (`memory.history`, `memory.lessons`), and setup says so. On a work machine, or when you'd rather
   not say which it is, the built-in local history index is the default and MemPalace isn't
   mentioned. See [PROFILES.md](PROFILES.md#the-memory-backend).
5. Offers to install the `gnomonkit` launcher (below) and records that setup is done.

Whether the machine is personal or work is asked once per machine, with the machine questions at the
session start (so a second machine is asked even when your personal profile came from the first), and
saved in the machine profile, never the personal one. "Rather not say" saves `unknown`, which counts as
work and isn't asked again.

Run it again any time: ask for "Gnomonkit setup", "change my preferences" or "what can I set".

**The `gnomonkit` command.** `profile show` lists every setting with its value, default and where it
comes from; `profile set key=value` changes one; `setup status` shows where setup stands, the
machine's kind and its memory backend. Claude
prints the full command when it needs you to run one (`python "<core>/gnomonkit.py" ...`, `<core>`
being the core plugin's install folder). To type plain `gnomonkit` in a terminal, run
`install-launcher` once: it writes `gnomonkit` and `gnomonkit.cmd` to `~/.gnomonkit/bin`, which find
the current core through Claude Code's plugin records, so they keep working after updates. It prints
the line that adds that folder to your PATH and never edits your shell profile itself.

**Working remotely or from the phone.** Setup asks whether you work remotely or from the phone a
lot. On yes, Claude runs `gnomonkit setup remote on`: Claude Code's `remoteControlAtStartup` goes on
in your user settings (the old file is kept as `settings.json.bak`), so every session you open, and
every background session Gnomonkit starts for you, is on your phone and at claude.ai/code while it
runs. The command then prints, for each project you dispatch sessions from, the two things that stay
yours: the grant that lets that project's sessions start, hand off to and stop their own, and the
folder trust. It also sets `dispatch.primary_client = "terminal"` (unless you pass `--client`), so a
primary Gnomonkit hands off is a background process the phone always sees, joined at the desk from its
claude.ai/code link or `claude attach`; a VS Code tab is on the phone only while VS Code has it open
(the trade-off table is in [PROFILES.md](PROFILES.md)). `gnomonkit setup remote off` turns the setting
back off; `setup status` shows both, and warns when you work remotely with a `vscode` client.

**Settings that grant permissions are yours to set.** Which background sessions Claude may start for
you, and which folders count as trusted, are permissions, so Claude doesn't choose them and setup
doesn't ask them. Setup ends by telling you to run, yourself, in a terminal:

```text
gnomonkit setup security
```

It refuses to run without a terminal, so an agent can't run it for you. It sets the orchestration
grants and then the folder-trust levels (both below). On Windows, if it says it isn't in a terminal
when run from Git Bash, run it from PowerShell or Windows Terminal.

### Orchestration grants

A grant says which projects a session may start, hand off to, message or stop sessions in. Session
dispatch (spawn, handoff, stop) and the interjector's `say`, `block` and `hold` (message) check it
before acting, when an agent asks from inside a Claude Code session. Run by you at a plain terminal,
nothing is checked: you need no permission from yourself.

`gnomonkit setup security` explains the choices and asks: which folders hold your projects, whether
a session in one project may act in your other projects (default no: only in its own), which actions,
and whether any project is off limits. It shows the file, asks you to type a word shown on screen, and
writes `~/.gnomonkit/grants.toml` (`GNOMONKIT_HOME` moves it):

```toml
version = 1

[[grant]]
from = "<projects folder>/*"             # where the requesting session runs
to = ["same-project"]                    # where it may act: its own project only
except = ["<projects folder>/<vault>"]   # optional: never these, whatever `to` says
actions = ["spawn", "handoff", "message", "stop"]
agents = ["claude"]
```

- **Patterns.** An absolute folder means that folder; `<dir>/*` any folder directly under it;
  `<dir>/**` any folder at any depth under it. In `to` (not `from`), `same-project` means the
  requesting session's own project and `*` means anywhere. Paths are compared after `~`, `..` and
  symlinks are resolved, with one separator, ignoring case on Windows. A session's project is the
  git repository it is in (the top level), else its folder.
- **Default deny.** No file, a file that can't be read or has a problem, an unknown `version`, or no
  matching grant: refused. A refusal says why, shows the grant that would allow it and the command
  that adds it. Claude gives you that command; it never runs it.
- **Agents.** `claude` is the only one checked today. Other names are accepted and ignored until
  supported.
- **Commands.** `gnomonkit grants list` shows the grants, numbered, and any problem with the file;
  `grants check --from DIR --to DIR --action A` says whether something would be allowed; `grants add
  --from P --to P --actions a,b` and `grants remove N` change the file; `grants deny-rules` prints the
  lines below. `add`, `remove` and `setup security` run only when stdin and stdout are a terminal and
  you type the word shown, and keep the previous file as `grants.toml.bak`.
- **The audit log.** Every decision, allowed or refused, and every change to the grants goes to
  `~/.gnomonkit/dispatch/audit.jsonl`, one JSON line each: time, from, to, action, agent, decision,
  the grant that matched, the session. Never a brief or a note's text. Dry runs are shown, not logged.

#### What is enforced

Checked on every call, by code:

- **Session-dispatch and the interjector.** `dispatch.py` (spawn, handoff, stop) and the
  interjector's say, block and hold check the grants before acting, and refuse without a grant.
- **Session commands an agent runs itself.** The core plugin's grants guard hook checks `claude
  --bg` (a session started in the folder the command runs in), `claude stop`, `kill`, `rm`,
  `attach` and `respawn`, typed into Bash or PowerShell, against the same grants. It also checks a
  SendMessage to another session. A message to the session's own subagents and teammates needs no
  grant. A folder change it can't follow, a session it can't find, or a session list it can't read
  means a refusal.
- **The grants file and folder trust.** The same hook blocks a tool call that writes the grants file
  (by any path, including relative paths, symlinks and links), names it or `~/.claude.json` in a
  shell command (reads included; `gnomonkit grants list` shows the grants), or runs a `gnomonkit`
  command that changes them. It also blocks a command that sets or clears the variables the check
  reads to tell which session is asking (`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_PID`) or
  where the grants file is (`GNOMONKIT_HOME`).
- **Changing the grants needs you.** `grants add`, `grants remove` and `setup security` run only at a
  real terminal, when you type the word shown.

Claude Code can refuse the same things itself. Add these to `permissions.deny` in your user settings
(`~/.claude/settings.json`), or with `/permissions` in a terminal session. Gnomonkit prints the full
list (`gnomonkit grants deny-rules`, which adds the PowerShell forms) and never edits settings files
for you:

```json
"permissions": {
  "deny": [
    "Edit(~/.gnomonkit/grants.toml)",
    "Write(~/.gnomonkit/grants.toml)",
    "Bash(claude --bg *)",
    "Bash(claude stop *)",
    "Bash(claude kill *)",
    "Bash(claude rm *)",
    "Bash(claude attach *)",
    "Bash(claude respawn *)"
  ]
}
```

Convention only (nothing checks it): what a program does once it is running. A script, a build
step or another tool that starts `claude` or writes the file itself isn't seen by the hook, and
neither is a one-shot `claude -p` run in the foreground. Keep the grants narrow and read the audit
log.

**Limits.** None of this is a sandbox. The hook reads a command the way a shell would, but a
command that builds a name or a command at run time can still get past it, and a call goes through
unchecked if Python can't start or the hook times out. The guard stops the plain cases (a mistaken
edit, an injected "add a grant" instruction, a session started straight from the shell); the
terminal rule makes a deliberate change need you; the audit log shows what happened.

### Folder-trust levels

The levels say where Gnomonkit may mark repositories trusted in Claude Code for you. They live in the
same file as the grants, under the same guard and the same terminal rule; `gnomonkit setup security`
asks for them after the grants:

```toml
[[trust]]
root = "<projects folder>"
level = "trusted"            # trusted | per-project | never
auto = true                  # trust new repositories found directly under it at session start
owners = ["<your GitHub account>", "<your organisation>"]
local_only = false           # also trust repositories with no remote

[[trust]]
root = "<projects folder>/<vault>"
level = "never"
```

- **`trusted`.** A repository root under it qualifies when its `origin` remote's owner is in `owners`
  (or, with `local_only = true`, when it has no remote). A bare name means that account on
  github.com; write `host/name` for an account on another host. The owner is read from the
  repository's `.git/config`. It says where a clone came from, not who wrote what is in it.
- **`per-project`.** Gnomonkit trusts nothing there; Claude Code asks you per repository, as before.
- **`never`.** Gnomonkit trusts nothing there, and session dispatch starts no session there, whatever
  the grants say (also when you run it at a plain terminal).
- **The most specific root wins**, so a folder inside a trusted root can be `per-project` or `never`.
  Paths are compared as in the grants. If the levels can't be read, nothing is trusted and session
  dispatch refuses to start sessions until the file is fixed.
- **`gnomonkit trust status`** shows each root's level and each repository under it: trusted, pending
  (it qualifies and `trust sync` would trust it), or not trusted and why, then the folders Claude Code
  trusts that no root covers, so no level applies to them. It only reads. The portal's Trust tab shows
  the same on a loopback portal ([PORTAL.md](PORTAL.md)).
- **`gnomonkit trust sync`** (terminal only, and you type the word shown) lists the repositories one
  level under each `trusted` root that qualify and aren't trusted yet, says why each qualifies, and
  marks them trusted. `--depth N` looks further down, which also finds repositories inside other
  repositories: each needs its own entry. It never touches a `per-project` or `never` root. Each one
  is checked again once you have typed the word: a folder swapped for a link or junction in the
  meantime, or a repository that no longer qualifies, is left alone and listed as not trusted.
- **At session start**, with `auto = true`, a core hook does the same for new repositories directly
  under the root and prints one line for each one it trusted, at most ten and then how many more. It
  skips a root whose folder hasn't changed since its last look and never blocks a session. Everything
  it does, the write included, fits in about five seconds; what it didn't reach waits for the next
  start. If Claude Code rewrote its file and dropped a key Gnomonkit had set, the next start puts it
  back, for a repository directly under an `auto` root at the path it trusted (one trusted with
  `trust sync --depth` deeper down, or a folder that has become a link, is left to `trust sync`).
  `GNOMONKIT_TRUST_AUTO=off` turns the hook off.
- **What is written.** Only `projects["<repository root>"].hasTrustDialogAccepted = true` in Claude
  Code's `~/.claude.json` (`CLAUDE_CONFIG_DIR` moves it); every other byte of the file stays as it
  was. Before writing, Gnomonkit checks that the file is JSON whose `projects` is an object (anything
  else is left alone), keeps a backup in `~/.gnomonkit/trust/backups/` (the last 10), reads the file
  again just before swapping in the new one and writes nothing if it changed, and reads it back
  afterwards. Every change goes to `~/.gnomonkit/trust/audit.jsonl`. The guard keeps agents out of
  `~/.gnomonkit/trust/` (the list of repositories Gnomonkit trusted, which the hook puts back, the
  backups and the log) as it does out of the grants file.
- **Checking it against a real session.** The tests use made-up files. To see Claude Code itself
  accept a repository Gnomonkit trusted, run `python tests/manual/trust_real_session.py --dry-run` from
  a Gnomonkit checkout to read the steps, then `--real` in a terminal: it trusts a throwaway repository
  under a temporary root, starts `claude --bg` there, checks the key survives the session and is put
  back when lost, then removes its entry. It edits the Claude Code file your sessions use (point
  `CLAUDE_CONFIG_DIR` at another logged-in config folder to keep yours out of it) and keeps a copy.

### Your history

Nothing to set up: from the first session start, core indexes your past sessions in the background
(locally, secrets scrubbed first), so Claude can search them before answering about past work, and
offers at session close to keep lessons from them. [HISTORY.md](HISTORY.md) covers searching,
keeping a project out, forgetting, and turning either part off.

## 2. Optional plugins

Install any of these the same way, for example `claude plugin install interjector@gnomonkit`:

- [interjector](../plugins/interjector/README.md): step into a running session, and have Claude
  search your history before asking you something it may already know.
- [supply-chain](../plugins/supply-chain/README.md): review what a dependency update actually ships.
- [portal](../plugins/portal/README.md): a read-only web dashboard over core's metrics; see
  [PORTAL.md](PORTAL.md).

## 3. Your profiles

See [PROFILES.md](PROFILES.md). Your personal profile is what first-run setup writes to
`~/.gnomonkit/personal.toml`; point the core plugin's `personal_profile` setting at a file in a
private overlay instead if you want it to follow you between machines. The first session on each
machine detects your OS, shells and GPU, asks what it can't detect and whether it is a personal or a
work machine (once), and saves a machine profile that never leaves that machine.

## 4. A private overlay

When you want your rules and profile to follow you between machines, or have private skills of
your own, keep them in an overlay: a private repository that is also a Claude Code marketplace.
`gnomonkit overlay init` makes one, `convert` brings an existing rules repository into the format,
`check` validates it and `link` prints the commands that connect it. None of them creates a remote or
pushes. See [OVERLAY.md](OVERLAY.md).
