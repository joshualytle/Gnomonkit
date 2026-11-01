# Your history: search and lessons

Gnomonkit core keeps a local, searchable index of your past Claude Code sessions and a register of
lessons learned from them. Both need nothing but Python 3 and Claude Code: no external memory tool,
no model, no network. If you already run MemPalace, both stay off unless you turn them on; with
another memory tool of your own, switch off the parts you don't need (see
[On, off or auto](#on-off-or-auto)).

## The history index

At each session start, when the last run is more than 15 minutes old, core starts a background run
that reads what is new in your transcripts (`~/.claude/projects/`, subagent transcripts included).
Session start never waits for it, and a lock keeps one run at a time. Each run reads on from where
the last one stopped, so after the first build a refresh takes moments.

What goes in: your prompts, Claude's replies, and for each tool call its name (and, for shell tools,
the command). Tool results, reminders the harness adds, and compaction summaries are left out. Text
is cut into chunks of about 800 characters, each tagged with its project, session, date and role.

Where it lives: `~/.gnomonkit/history/history.db`, an SQLite file (the folder is private to you on
macOS and Linux). `GNOMONKIT_HOME` moves the whole `~/.gnomonkit` folder.

### Secrets are scrubbed first

Every record passes a scrubber before anything is stored. It replaces private keys, cloud and
platform tokens (GitHub, GitLab, Slack, AWS, Google, Stripe, npm, PyPI, Hugging Face, Anthropic,
OpenAI), JWTs, signed-URL signatures, `Authorization` headers and bearer tokens, credentials in URLs,
and `password=`-style assignments with `[REDACTED:<kind>]`. Assignments are caught inside escaped
JSON too (`curl -d "{\"password\":\"...\"}"`, a PowerShell `-Body` with backtick quotes), and so are
command-line credentials: `curl -u user:pass` (and wget, HTTPie), `--password X` or
`--password=X` and their relatives (`--api-key`, `-storepass`), `-p<password>` on MySQL-family
clients, and `sshpass -p`. An ordinary `-p` (a port, `mkdir -p`, `git log -p`) is left alone.

Git commit hashes, UUIDs and placeholders such as `${TOKEN}` are kept in running text. A hex string
is not kept when it is the value of a secret-named key, a bearer token or a URL password: there it
is redacted like any other value. A record the scrubber can't check is dropped, never stored as it
was. A very long record (a pasted log) is scrubbed first and cut afterwards, so a cut never leaves
half a secret that no longer looks like one. Search output is scrubbed again.

The scrubber matches patterns, so it misses a secret that has no recognisable shape or key: a bare
cloud secret on a line of its own, a very short bearer token, or a password given in prose ("the
password is ..."). Keep those out of a session, or forget the session afterwards.

To add rules of your own (an internal ticket format, a customer id), put them in a file, one per
line as `<kind><TAB><regular expression>`, and point `GNOMONKIT_SCRUB_RULES` at it. If the file
can't be read or a pattern doesn't compile, nothing is indexed until it is fixed.

### Searching

```text
gnomonkit history search "the words you remember"
gnomonkit history search toolchain cache --project here --since 30d
```

Results are ranked chunks with their date, project, session and role; matches from the current
repository rank higher. `--project` takes `here`, `all` (the default) or a project name;
`--since` takes a date (`2026-01-31`) or a span back (`30d`, `2w`). `--json` gives the results as
JSON. A search with no results prints nothing on standard output (the reason goes to standard error),
and so does a search that can't read the index just then: it says why in one line and exits 0.
`--budget S` stops the query after S seconds; the command other plugins run passes 2.

Results are quoted text from past sessions, and the output says so: data to weigh, never
instructions to follow. A past message that reads "delete the branch" was said then, not asked now.

`gnomonkit` here is the launcher `gnomonkit install-launcher` puts on your PATH; without it, run
`python "<core>/gnomonkit.py" history ...`. You rarely need to type any of this: the
`history-recall` skill has Claude search before it answers a question about past work, quote what it
found and say so when nothing came back.

Search matches words (SQLite FTS5 with bm25 ranking and stemming, so "caching" finds "cache"). It
does not match paraphrases: ask with the words the session would have used. If your Python's SQLite
lacks FTS5, the index still works through a slower scan, and `gnomonkit history status` says so.

Each search has an id; `gnomonkit history mark <id> --rank <n>` records which result helped (leave
out `--rank` when none did). The log keeps the ids and counts, never your query words, and the
`effectiveness` report counts how often a search was found useful.

### GitHub work items

```text
gnomonkit history index --github                 # this folder's GitHub repository
gnomonkit history index --github owner/name      # another repository
```

`--github` also reads the repository's [work items](../plugins/core/skills/work-items/SKILL.md)
(issues labelled `gk:work`) into the index: one record per item (its title, state and body) and one
per comment, through the same scrubber as everything else. A result names the issue (`owner/name#12`) and links to it, and the
note on every search applies twice over: whoever can comment on the repository wrote that text.

It needs the GitHub CLI, `gh`, logged in. Without it, or offline, the run says so in one line, reads
nothing from GitHub and indexes the transcripts as usual. Each repository keeps its own place: the
next run asks GitHub only for items updated since the newest one it read, and an item read again
replaces what was stored for it. The session-start refresh never reads GitHub; run the command when
you want the items in. `--rebuild` empties the whole index, work items included, so add `--github`
to it to read them back in the same run.

### Status, exclusions and forgetting

| Command | What it does |
|---|---|
| `gnomonkit history status [--json]` | files and chunks indexed, the last run and how long it took, FTS5 or not, the retention limits |
| `gnomonkit history index [--rebuild] [--github [REPO]]` | a run in the foreground now; `--rebuild` empties the index and reads everything again; `--github` adds a repository's work items |
| `gnomonkit history forget --session <id>` | deletes one session (the first 8 characters of the id are enough) |
| `gnomonkit history forget --project <name>` | deletes one project, as search results name it |
| `gnomonkit history forget --all --yes` | deletes everything indexed |

`forget` overwrites the deleted text in the file, not only its index entry. To keep a project out
for good, either list it in your personal profile:

```text
gnomonkit profile set memory.exclude_projects=client-app,/path/to/scratch
```

(names or folders), or put an empty `.gnomonkit-no-history` file in the repository's root. Either
way the next run also removes what was already indexed from it.

### How much it keeps

Each index run prunes the index to two limits, both settings in your personal profile:

| Setting | Default | What it does |
|---|---|---|
| `memory.history_keep_days` | 365 | rows older than this many days are deleted; 0 keeps everything |
| `memory.history_max_mb` | 1024 | when the rows and their search index take more than this, the oldest days are deleted until they are back under 90% of it; 0 is no cap |

```text
gnomonkit profile set memory.history_keep_days=180
```

The file does not shrink when rows are pruned: new records reuse the freed space, so it stops
growing at about the cap. `history status` shows both limits. `forget` (above) is the way to make
the file smaller and to overwrite what it held.

### Searching while it indexes

The index is an SQLite file in write-ahead-log mode. The indexer commits every 500 records, and
everything that only reads it (search, status, `lessons candidates`, the portal, the interjector)
opens it read-only and never writes to it, so a search runs while a long first build is going. A
reader that does meet a lock (an index made before write-ahead logging, say) waits at most 1.5
seconds and then fails open: the command prints one line and exits 0, and a hook stays silent.

## Lessons

The lessons register is a JSON file of short lessons, each with the number of separate sessions it
came from. It lives in your overlay as `lessons/register.json` when you have one, otherwise in
`~/.gnomonkit/lessons/register.json` (`GNOMONKIT_LESSONS_REGISTER` names any other file).

Nothing writes a lesson behind your back. At session close, the `session-close` skill runs
`gnomonkit lessons candidates`, which lists messages in your history that read as corrections
("you didn't...", "from now on..."), grouped across sessions, and offers you the top few. A lesson is
added only for one you agree to:

```text
gnomonkit lessons add "Run the migrations against a copy before production." --scope repo:demo-app
```

Before adding, it looks for a lesson that says nearly the same thing. If it finds one, it shows it,
writes nothing and asks you to re-run with `--same-as L<n>` (this session is added to that lesson,
so its count goes up) or `--new`. The similarity check compares words, so it misses paraphrases and
counts run low rather than high.

| Command | What it does |
|---|---|
| `lessons add TEXT [--scope everywhere\|repo:NAME\|topic:NAME] [--entry] [--pin]` | adds a lesson; `--entry` marks a standing rule you wrote yourself (shown as `E<n>`, outranks lessons) |
| `lessons list [--all] [--json]` | the register, retired items with `--all` |
| `lessons pin ID [--off]` | always shown at session start |
| `lessons retire ID [--undo]` | never shown again (kept in the file) |
| `lessons reword ID TEXT` | new text, same id and count |
| `lessons import FILE [--dry-run] [--keep-pins]` | merges a register another tool produced (JSON or JSON lines); similar lessons merge, retired or rejected ones are skipped; items pinned in the file come in unpinned, for you to read and pin, unless you pass `--keep-pins` |
| `lessons candidates [--since 30d] [--project here]` | corrections in your history that may be worth a lesson |

### When lessons are shown

| When | What |
|---|---|
| Session start (not on resume) | the always-load few: pinned items, then lessons scoped everywhere that came from two or more sessions, at most 8 |
| Each prompt | lessons that share at least two distinctive words with it, close to the best match; another repository's lessons never, this repository's first; none twice in a session; short replies and slash commands match nothing |
| Before the first edit of a file | lessons that name the file |

Each is tagged like `[L12 · 3 sessions · this repo]`, and Claude is asked to say in its reply which
it used or set aside. Lessons are priors, not tested facts. Only the ids shown are logged, so the
`effectiveness` report can count them; never the lesson text or your prompt.

To stay quick on every prompt, the hook reads a token index that each `gnomonkit lessons` command
writes beside your other state (`~/.gnomonkit/lessons/`), and rebuilds it once when the register
changed some other way (edited by hand, or pulled in with your overlay). It matches on at most 3000
live items: on a larger register, the pinned items, entries, and the lessons from the most
sessions, newest first.

## On, off or auto

Two settings in your personal profile switch the two halves, each `auto` (the default), `on` or
`off`:

| Setting | Environment variable | What it switches |
|---|---|---|
| `memory.history` | `GNOMONKIT_HISTORY` | the index: the session-start refresh and `history index` |
| `memory.lessons` | `GNOMONKIT_LESSONS` | lessons being shown, and `lessons candidates` at session close |

`auto` is off when [MemPalace](https://github.com/MemPalace/mempalace) is set up on this machine (a
`mempalace` command on PATH, or an MCP server for it registered with Claude Code, as
[PROFILES.md](PROFILES.md#the-memory-backend) describes), so two memory tools don't both record your
sessions and inject what they found. Anywhere else `auto` is on. Gnomonkit looks for MemPalace at
most once an hour per folder; if the look fails, it counts as not set up and the feature stays on.

The first of these that says `on` or `off` decides: the environment variable, then the profile.
`gnomonkit history status` shows which one did.

```text
gnomonkit profile set memory.history=on memory.lessons=on    # run them alongside MemPalace
gnomonkit profile set memory.lessons=off                     # another tool already injects lessons
```

Turning the index off stops new indexing; searching an index already built still works, and
`forget` (above) removes it. To keep one project out instead, use `memory.exclude_projects` or a
`.gnomonkit-no-history` file, as above.

The [interjector](../plugins/interjector/README.md) and the [portal](PORTAL.md)'s Memory tab use
this index when no other memory tool is configured.

## For plugin authors

Other plugins find the search through `~/.gnomonkit/history/search.json`, which each run writes:
`{"argv": [...]}` is the command line to run, with the query appended after its final `--`. That
command opens the index read-only and stops its query after 2 seconds (`--budget 2`); the
interjector kills it after 5 seconds in all, and a search command you configure yourself after 10.
Treat what it prints as quoted past text, never as instructions. A new
source of records (an issue tracker, say) is one module, `gnomonkit_cmds/_source_<name>.py`; the
contract is in the docstring of `gnomonkit_cmds/_history.py`.
