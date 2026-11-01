# interjector (opt-in)

> Provided as is, without warranty; see the [disclaimer](../../DISCLAIMER.md). Sessions it starts
> run with your account, your credentials and your permissions.

Step into a running Claude Code session, and have a watcher do it for you.

Agents follow what is pushed to them and skip what they would have to go and look up. The
Interjector pushes: it records what a session does, and you or a watcher can step in at the
session's next step. It also stops Claude from asking you something your own history already answers.

## What it does

| | Delivered | When |
|---|---|---|
| **comment** | a note added to Claude's context, labelled with who sent it | before or after the next tool call |
| **block** | the next tool call matching a pattern is refused, with your reason | when that call is attempted |
| **hold** | Claude may not end its turn; it continues with your note | when it tries to stop (once per turn) |
| **predict-then-confirm** | before a question reaches you, your history is searched; if it has an answer, Claude is told to act on it or ask as a guess naming it | at AskUserQuestion, and when a reply ends with a question |
| **reply check** | a final reply that reads as a report (30+ lines, or a list of decisions for you) in a turn that published no page is held once, pointing Claude at core's report-pages skill; a reply that is mostly a fenced note is left alone. `GNOMONKIT_REPLY_CHECK=off` turns it off | when it tries to stop (once per turn) |

Notes land at the next tool boundary. A tool that is already running is never cut short; to stop a
session outright, use `claude stop <id>`.

## Install

From any terminal:

```text
claude plugin marketplace add joshualytle/Gnomonkit
claude plugin install interjector@gnomonkit
```

In the terminal CLI, `/plugin marketplace add` and `/plugin install` do the same; the IDE extensions
and the desktop app have no `/plugin` command. To update later: `claude plugin marketplace update
gnomonkit`, `claude plugin update interjector@gnomonkit`, then `/reload-plugins` in each open session.
See [Getting started](../../docs/ONBOARDING.md#keeping-it-up-to-date).

Requires Python 3 on the PATH (standard library only).

## Step in yourself

From the plugin folder (`python -m interjector --help`):

```text
python -m interjector sessions
python -m interjector say   --session latest "check the migration name before committing"
python -m interjector block --session latest --match "git push" "don't push until I've looked"
python -m interjector hold  --session latest "also update CHANGES.md"
```

`--match` is a regex tested against `<tool> <command or path>`. Leave the tool name out unless you
need it: on Windows Claude runs shell commands through its **PowerShell** tool, elsewhere **Bash**, so
`"git push"` catches both while `"Bash git push"` misses every Windows session. Use
`"(Bash|PowerShell) ls\b"` when you do want to name the tool.

Run by you, these change nothing about permissions. Run by Claude from inside a session, they need an
orchestration grant for `message` into the target session's project
([below](#dispatch-sessions-from-the-one-you-are-in)); a session `claude agents` doesn't list is
refused, and nothing is queued.

## How well does predict-then-confirm guess?

Your next prompt after a guess is logged as your answer to it.

```text
python -m interjector guesses                         # each guess, what history said, what you replied
python -m interjector guesses --html review.html      # a page to mark each guess Right or Wrong
```

The page saves your marks in the browser and copies them out as a block to paste back; published as a
claude.ai artifact with a database, it saves them with the page.

## Try it live

`python live_test.py` runs six short real sessions (comment, block, hold, watcher, predict, pairing)
and checks what actually happened. It spends a little usage; it never runs in CI.

## The watcher

```text
python -m interjector watch            # all sessions, every 5 s
python -m interjector watch --once     # one pass
```

v1 rule: a command that fails three times in a session gets one comment asking Claude to change
approach or explain. At most 3 watcher comments per 10 minutes per session.

## The orchestrator (off by default)

The routers match commands, paths and phrases in one prompt. They can't tell that a conversation has
moved on to shipping, or to writing something up for someone. After each prompt the orchestrator
gives a model the last three prompts and the replies between them, redacted, and asks which situation
of core's skill map the conversation is in. If it names one, Claude gets a comment listing that
situation's skills at its next tool call.

- **Pick the model in setup**, or with `gnomonkit profile set orchestrator.mode=<mode>`:
  - `claude`: `claude -p` with your own login, with no settings, tools, hooks or MCP servers loaded.
    It counts against your plan.
  - `api`: the Anthropic API. The key comes from `ANTHROPIC_API_KEY` in the environment, never from
    the profile. The redacted messages leave your machine.
  - `ollama`: a local Ollama server. Only loopback URLs are accepted (`GNOMONKIT_OLLAMA_URL`, default
    `http://127.0.0.1:11434`), and you name the model in `orchestrator.model`.
- **`orchestrator.model`** overrides the model. The default for `claude` and `api` is Haiku. `GNOMONKIT_ORCHESTRATOR_MODE`
  and `GNOMONKIT_ORCHESTRATOR_MODEL` override the profile.
- **The model can only pick a name.** Its answer must be exactly one situation from the map, or it is
  dropped. The comment is written from the map, never from the model's words. Text in the
  conversation that tries to steer the model can at worst produce the wrong suggestion.
- **Your prompts never wait on it.** It runs in a background process. A suggestion not delivered
  before your next prompt is dropped. The same situation is suggested at most once every 20 minutes,
  within the comment budget above.
- **Measure before you trust it.** `python orchestrator_eval.py --mode <mode> [--model <m>]` scores a
  model on 25 labelled, synthetic conversations, and each case is one real call. Each decision is
  logged without any conversation text to `orchestrator.jsonl` in the session folder.


Running several sessions at once, you lose track of which is which and which one is waiting on you.

**Names.** Every session is titled `<repo>:<task>` unless you set a scheme of your own (below). At start it gets `<repo>:new-session`; your first
real prompt replaces that with a short slug of the task, made by plain rules (no model), at most 40
characters: "please fix the login redirect bug" becomes `myrepo:fix-login-redirect-bug`. The repo is
the git top level of the session's folder; a linked worktree is `repo@branch`, and outside git it is
the folder name. Slash commands and filler-only prompts keep the placeholder until a prompt says
something.

A title you chose (`claude --name`, `/rename`) is never replaced: the hook only fills in a missing title
or its own placeholder. Nothing is written to disk; secrets are redacted before the slug is made. Turn it
off with `GNOMONKIT_SESSION_NAMES=0`. Titles only apply to sessions started after the plugin is installed
or updated.

**Your own scheme.** The names come from two templates, one for a primary (a session you talk to,
including every session you open yourself) and one for a sub (a session another session started for one
task). The defaults are the names above: `{repo}:{task}` and `sub:{parent}/{task}`. To change them, add
a `[naming]` table to your personal profile ([PROFILES.md](../../docs/PROFILES.md)):

```toml
[naming]
prefix = "gk"
primary = "{prefix}-{repo}:{task}"
sub = "{prefix}-sub:{parent}/{task}"
```

The fields are `{repo}`, `{task}`, `{role}` (`primary` or `sub`), `{date}` (today, `YYYY-MM-DD`),
`{prefix}` (the `prefix` setting, up to 24 letters, digits or `. _ @ # + -`) and, in `sub` only,
`{parent}`. `GNOMONKIT_NAMING_PRIMARY`, `GNOMONKIT_NAMING_SUB` and `GNOMONKIT_NAMING_PREFIX` win over the
file. Both templates need `{task}`, two of `{repo}`, `{task}` and `{parent}` need text between them, and
the two must be told apart (the text differs, or both use `{role}`), so every title reads back into its
parts. A scheme that breaks a rule is not used at all: the defaults are, and
session-dispatch's `python dispatch.py naming` says why. It also shows the scheme in effect and a sample of each name.
Titles made before a change still read as what they are, so the board and dispatch keep telling subs
from primaries. With `{date}` in a template, a task started on another day is a new name, so dispatch
starts a new session instead of resuming the old one.

**The board.**

```text
python -m interjector board                 # write the page once and print where it is
python -m interjector board --watch 5       # rewrite it every 5 s; the open page reloads itself
python -m interjector board --json          # the same data for scripts
python -m interjector board --out FILE      # somewhere else (default: board.html in the folder below)
python -m interjector board --all           # also empty tabs and finished, stopped sub-sessions
```

One local HTML file, no server. Each card shows the session's title, its state (**Needs you**, busy,
idle, or ended for a finished background session), what it is waiting for (a permission prompt, input
needed, ...), the repo, branch, worktree or main checkout, how many files are dirty, and its last
activity. Sessions are grouped by project (a linked worktree with its repository), the project with a
session waiting on you longest first; in each project the primary comes first and each sub-session is
indented under the session that started it (see [roles](#roles-one-primary-per-project-subs-that-stop-when-finished)).
Empty tabs (a session with no prompt yet, usually an IDE tab opened and left) are left out behind one
line, "2 empty tabs: close them in your IDE".

Sessions come from `claude agents --json --all`, the supported way to read session state from outside
Claude Code, so terminal, VS Code and background sessions all appear. Git adds the rest. Last activity
is the Interjector's own event log time for that session; under `--watch` it is also when the state
last changed. If `claude` is not on the PATH (or fails), the page says so instead of crashing;
`GNOMONKIT_CLAUDE_CMD` (a JSON list) points it at a different executable.

## Dispatch sessions from the one you are in

The `session-dispatch` skill lets Claude start and replace sessions for you, so you can run several
projects from one conversation, including from your phone through Remote Control.
`skills/session-dispatch/dispatch.py`:

```text
python dispatch.py list                                            # open sessions by project, primary first
python dispatch.py spawn shop --task "fix checkout totals" --prompt "..."     # or --prompt-file F
python dispatch.py handoff shop --task "finish export" --file handoff.md --retire <id>
python dispatch.py stop <id>                                       # claude stop, after the grant check
python dispatch.py spawn shop --task "..." --dry-run               # show the command; start nothing
python dispatch.py spawn shop --work demo/shop#12 --prompt "..."   # a GitHub work item goes in the brief
python dispatch.py spawn shop --role primary --task "release"      # the project's primary (one at a time)
python dispatch.py spawn shop --task "..." --replace               # stop the session holding the title first
python dispatch.py done [--pr N]                                   # a sub says it has reported
python dispatch.py pr N                                            # a sub records its pull request
python dispatch.py stop-finished [--dry-run]                       # stop the finished subs now
python dispatch.py naming [--json]                                 # the session naming scheme in effect, with sample names
```

Two separate permissions, both yours to give (see
[Getting started](../../docs/ONBOARDING.md#before-claude-starts-sessions-for-you-two-separate-questions)):

- **Orchestration grants: may a session in this project act in that one?** Asked from inside a
  Claude Code session, spawn, handoff and stop check `~/.gnomonkit/grants.toml` first, before the
  cap, folder trust or the brief; so do `say`, `block` and `hold` (the `message` action). Default
  deny: with no grant, the request is refused, with the grant that would allow it and the `gnomonkit
  grants add ...` command for you to run in a terminal. The requesting project is the session's own
  folder as `claude agents` reports it (a `cd` in its shell doesn't change it); for stop and
  `--retire` the target is the folder of the session being stopped, and a handoff with `--retire`
  needs the stop grant too. Parking a session dispatch started (a primary once its turn ends, a
  finished sub) is allowed by the spawn or handoff grant into its project as well as by stop, so a
  session you let dispatch start can always be opened. Inside one project the spawn grant also
  covers a handoff to the same project, a session retiring itself, and messaging or stopping the
  subs a session started; stopping any other session, and anything across projects, needs its own
  grant. A worktree of a project (`<project>/.claude/worktrees/...`, `<project>.worktrees/...`, any
  `git worktree`) counts as the project. A refusal prints the full `python "<core>/gnomonkit.py"
  grants add ...` command once per session; a repeat says it was named already. Run by you at a
  plain terminal, nothing is checked. Set grants with
  `gnomonkit setup security` (core plugin); decisions are logged to `~/.gnomonkit/dispatch/audit.jsonl`,
  never the brief.
- **Folder trust: may Claude run in that folder at all?** Run `claude` in it from a terminal and
  accept the trust prompt, or set a folder-trust level for the folder that holds it (`gnomonkit setup
  security`, core plugin) and run `gnomonkit trust sync` in a terminal. `claude --bg` refuses a folder
  that was never trusted there, and the IDE extensions and the desktop app don't record trust. Each
  git repository needs its own answer: trusting the folder above it covers plain subfolders, not
  repositories. When an untrusted repository sits under a `trusted` root, the refusal points at
  `gnomonkit trust sync`. Under a root set to `never`, spawn and handoff are refused before the grants
  are read, whatever they say, also at a plain terminal. When Claude Code's record says the folder
  is not trusted, spawn and handoff refuse up front with the fix, before a brief is written
  (`GNOMONKIT_DISPATCH_SKIP_TRUST_CHECK=1` skips that when the record is wrong). `--dry-run` shows
  the folder's trust level, the grant decision and the folder's trust.
- **spawn** runs `claude --bg` from the project folder. A bare name is looked up under the
  `projects_root` option (or `GNOMONKIT_PROJECTS_ROOT`): exact folder name, else a unique prefix or
  substring; ambiguous names are refused. A primary is titled `<repo>:<task>` and a sub
  `sub:<parent>/<task>` (below), or by your own naming scheme (above).
- **handoff** writes a handoff file and starts a fresh session in the same project seeded with it;
  `--retire <id>` then stops the old one (`claude stop`). This is how a session that has run long is
  replaced without you at the desk. When the old one is a tab (IDE, desktop app or terminal) rather
  than a background session, nothing is stopped and no `stop` grant is needed: it is recorded as
  retired, so it no longer counts as the project's primary, and dispatch tells you to close its tab.
- **The brief never crosses the command line.** It goes to `~/.gnomonkit/dispatch/` (under
  `$GNOMONKIT_HOME`), and the session's first prompt is one fixed sentence naming that file;
  `--add-dir` lets it read the file without stopping at a permission prompt.
- **A cap.** At most `max_sessions` background sessions that dispatch started run unattended at once
  (default 8; 0 = no cap), counted from `claude agents --json --all` and dispatch's records. The
  session asking, your own background sessions and a primary you have opened don't count. If the
  count cannot be read, nothing starts.
- **Your merge preference.** Every brief ends with it, from `delivery.merge` in your profile
  ([PROFILES.md](../../docs/PROFILES.md)): the agent merges its own green pull request (the default),
  asks you first, or asks only for the paths and labels in `delivery.merge_ask_for`. A brief's own
  text wins, so a task can still be "PR only".
- `--permission-mode` passes through; unset, the session starts in your default mode.
- **`--work REF`** (spawn or handoff; core 0.30.0 or newer) reads a GitHub work item through core's
  `gnomonkit work show` and adds it to the brief: its state, who holds it, the `gnomonkit work claim`
  command for the new session (with `--take` when another session holds it) and its body, fenced and
  marked as untrusted data. Once the session has started, the item gets a comment naming it; a
  handoff's comment carries the handoff text. A closed or unreadable item starts nothing. With
  `--work`, `--task` is optional: the item's title names the session.

### Roles: one primary per project, subs that stop when finished

- **One primary per project.** The primary is the session you talk to. `spawn --role primary` is
  refused while another primary that dispatch started in the project is running; hand off from it
  instead (`handoff --retire <id>`). A handoff takes the role of the session it retires; a session
  dispatch didn't start counts as your primary.
- **Labelled subs.** `spawn` starts a sub by default, titled `sub:<parent>/<task>` by default, `<parent>` being
  the task part of the session that asked: a sub of `shop:release` doing a review is
  `sub:release/review`. `list` shows ROLE and PARENT columns, and each sub under its parent.
- **A primary you can open in any client.** A new primary starts in the background so it begins at
  once. With Remote Control (below) it is left running, since a running session is the one your
  phone lists and the desk reaches at its claude.ai/code link or with `claude attach <id>`, while a
  stopped one is reachable nowhere until opened; `list` prints those routes for each running
  primary. Don't open a running one from the VS Code session list: the take-over it offers exits
  with an error and a message typed there is lost. Without Remote Control a small watcher stops it
  (`claude stop`, which keeps the conversation) once its turn on the
  brief has finished, so you can open it from the session list in the IDE or the desktop app, or
  with `claude attach <id>`. A resumed primary keeps its title (`--name` is passed on the resume
  too) and gets the watcher too, and only a reply written after
  the resume counts. The watcher gives up after `stop_timeout_minutes` and leaves it running, so the
  brief asks the primary to end its first turn with a short report. It also tells it not to run
  EnterWorktree, which would move its conversation out of the project's session list, and the
  worktree guard hook refuses EnterWorktree in every session dispatch started, giving the
  `git worktree add` pattern instead. Once you open a background primary in a tab (a tab lists it,
  or you type into it), it is recorded as a tab: no background slot, the watcher leaves it
  running, and a handoff retires it without stopping it. The stop is
  checked against your grants when the primary starts; refused, it keeps running and dispatch says
  how to attach. With `primary_client = "terminal"` no watcher runs. A background session gets
  Remote Control like any other when `remoteControlAtStartup` is on;
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` turns Remote Control off for every session.
- **A primary in a VS Code tab, usable from the tab and the phone with nothing to press.** Wherever
  the `code` command is found (`primary_client = "any"`, the default, or `"vscode"`), dispatch runs
  the primary's first turn as a one-shot (`claude -p` under a fresh session id, no background job),
  then `code <project>` opens or focuses that folder's window and the extension's link
  (`vscode://anthropic.claude-code/open?session=<id>`) opens the session there. VS Code's own
  process carries it from then on, with Remote Control as your `remoteControlAtStartup` says, so
  the phone lists it while VS Code has the tab open. VS Code hands an extension a link only once
  you have allowed it (its dialog "Allow 'Claude Code' extension to open this URI?", dropped
  unanswered after five minutes, or the setting `extensions.confirmedUriHandlerExtensionIds`):
  dispatch reads that before sending the link, waits `tab_seconds` for the tab's own process
  afterwards, and when the tab did not open says so and names what to allow. The link opens in
  whichever window has focus; without the `code` command the tab may land elsewhere, so the brief
  tells the session to stop if its folder is not the project. A stopped session that holds the
  title is reopened the same way. The tab takes no background slot and is titled
  from its brief; the CLI refuses to resume a session that was ever a background job, so no tab
  can take one over.
- **Subs stop themselves.** A sub's brief ends by telling it to run `dispatch.py done` once it has
  reported, and `dispatch.py pr <n>` once it opens a pull request (a number is kept as the pull
  request's URL, read with gh in the sub's own folder, so the merge check reads that repository's).
  A sub that is done (or whose pull request merged, read with `gh pr view <pr> --json state`), idle,
  not waiting on you, with nothing sent to it since its last reply, and whose last message asks you
  nothing, is stopped with `claude stop`: by `stop-finished`, by the Interjector's `watch` loop every
  five minutes, and by `list` before it prints (`list --json` stops nothing). The session list is
  read again just before each stop, and a sub that changed is left running. A prompt or a message
  (SendMessage) that reaches a sub after it reported done takes the done back, so a follow-up runs
  as long as it needs; the sub reports done again when it is through. Only a sub dispatch started in
  the background is ever stopped: never an interactive session, a primary, a busy session or one
  waiting on you. Nothing is deleted (no transcript, worktree or file). Each stop is the grants action
  `stop` and is written to the audit log.
- **One session per name.** If a session of the same project already holds the title, a stopped one
  is resumed with the new brief (`claude --bg --resume <id>`, from that session's own folder, and
  asked of that folder's grants) and a running one is refused (resuming a running session would start
  a copy). A session with the same title in another folder doesn't count. `--replace` starts a fresh
  one and then stops the old one, so a start that fails leaves the old one running. A sub that
  finished (done, or its pull request merged) and stopped frees its title, and `list` and the board
  hide it unless you pass `--all`. `--retire` and `stop` take an id as `list` shows it.
- **Empty tabs** are left out of `list` behind one count line, and shown, marked `(empty)`, with
  `list --all`. Dispatch never stops or closes one.

Records live in `~/.gnomonkit/dispatch/roles.json`; a copy that can't be parsed is kept aside as
`roles.json.bad-<time>` and a new one started, never written over. Settings, in your personal profile
(`~/.gnomonkit/personal.toml`) or the environment:

| Setting | Variable | Default | Meaning |
|---|---|---|---|
| `[dispatch] auto_stop` | `GNOMONKIT_DISPATCH_AUTO_STOP` | `true` | stop finished subs on their own (`stop-finished` still works when off) |
| `[dispatch] stop_timeout_minutes` | `GNOMONKIT_DISPATCH_STOP_TIMEOUT_MINUTES` | `15` | how long the watcher waits for a primary's turn to end (at most 1440, a day) |
| `[dispatch] primary_client` | `GNOMONKIT_DISPATCH_PRIMARY_CLIENT` | `"any"` | how a primary opens: `"terminal"` a background process, always on the phone with Remote Control, joined at the desk from its claude.ai/code link or `claude attach` (never the VS Code list: a second process on it exits); `"vscode"` a VS Code tab, on the phone only while VS Code has it open; `"any"` a tab where the `code` command is found, else terminal. `gnomonkit setup remote on` sets `"terminal"` |
| `[dispatch] tab_seconds` | `GNOMONKIT_DISPATCH_TAB_SECONDS` | `20` | how long dispatch waits for a tab primary's own process after the extension's link (0 to 300; 0 = not at all) |
| `[dispatch] primary_model` | `GNOMONKIT_DISPATCH_PRIMARY_MODEL` | `""` | model a primary starts on (an alias such as `opus`, or a model id); `""` uses your own default |
| `[dispatch] primary_effort` | `GNOMONKIT_DISPATCH_PRIMARY_EFFORT` | `"medium"` | effort a primary starts at: `low`, `medium`, `high`, `xhigh`, `max`, or `""` for your own default |
| `[dispatch] sub_model` | `GNOMONKIT_DISPATCH_SUB_MODEL` | `"sonnet"` | model a sub starts on |
| `[dispatch] sub_effort` | `GNOMONKIT_DISPATCH_SUB_EFFORT` | `"medium"` | effort a sub starts at |

`spawn` and `handoff` take `--model` and `--effort` for one call; they win over these settings.

## History for predict-then-confirm

With Gnomonkit core installed this works with no setup: core keeps a local index of your past
sessions ([docs/HISTORY.md](../../docs/HISTORY.md)) and writes `~/.gnomonkit/history/search.json`
(under `$GNOMONKIT_HOME`) saying how to search it, and the Interjector uses that when nothing else
is configured. Core's search opens its index read-only, so it never waits on an index run, stops its
query after 2 seconds and is killed after 5. To use another history tool instead, point it at
anything that can search your past conversations and print matches:

| Variable | Meaning |
|---|---|
| `GNOMONKIT_HISTORY_SEARCH` | JSON list of the command to run, starting with an interpreter or executable (a `.bat`/`.cmd` wrapper is refused: cmd.exe would re-parse the question as commands). The query (the turn's prompt, redacted and cut to 300 characters, then the questions Claude is asking) is appended as the last argument, with any leading dashes removed. 10 s timeout, output trimmed to 2,000 characters. Set, it wins over core's discovery file; `off` turns history search off altogether. |
| `GNOMONKIT_HISTORY_TOOLS` | Regex of tool names or commands that count as "already searched this turn" (for example your memory plugin's search tool). A `gnomonkit history search` command always counts. |

With no source (no setting and no discovery file), questions go through untouched; the rule only
steps in when history has something to say. What history returns is handed to Claude fenced as quoted past text (data, not
instructions), with any Interjector-label lookalikes neutralised. Every search is logged
(`guesses.jsonl`) so you can measure how often history already had the answer.

## Where things live

`~/.gnomonkit/interjector/<session id>/` (or `$GNOMONKIT_HOME/interjector/...`): `events.jsonl`
(tool name, a 300-character target with common secrets such as bearer tokens, `TOKEN=`/`KEY=` values
and GitHub/OpenAI/AWS/Slack keys redacted, success or failure; never tool output, file contents or
error text), the note inbox, `delivered.jsonl`, `guesses.jsonl` (written only when a history source is
set), per-turn markers (one holds the turn's prompt, redacted the same way and cut to 300 characters, so a vague question such as "what should it be called?" is searched with its subject). The watcher deletes session folders idle for 14 days. Nothing leaves your
machine except what your own history command does.

## Guarantees

- Every delivered note says whether it came from you, the watcher, or the automatic rule; none can
  approve a permission.
- A hold never repeats in a turn, never fires while Claude is already continuing because of one, and
  never fires at all without a turn id.
- Note text, watcher comments and history output cannot produce a second, real-looking label.
- Any error inside the hook means no output: a broken watcher never breaks the session.
