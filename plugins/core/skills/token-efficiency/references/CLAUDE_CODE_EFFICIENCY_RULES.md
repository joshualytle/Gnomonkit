# Claude Code Efficiency Rules — Portable Ruleset

> Drop this file into any repo as `CLAUDE.md` (or merge the relevant sections
> into an existing one). These rules keep Claude Code sessions lean, reduce
> token burn, and make multi-session workflows reliable across all your
> projects.

---

## The core mental model

**Claude re-reads the entire conversation on every message.** Message 1 is
cheap. Message 30 means Claude re-processes 29 previous exchanges before
it even starts thinking about your new question. One developer measured
that **~98.5% of tokens in a long session were spent re-reading history;
only ~1.5% produced new output.**

Every rule below works because it either:

- Makes each individual message cheaper, or
- Prevents the conversation from growing in the first place.

If you remember nothing else, remember this: **long, drifting sessions are
the enemy**. Short, focused sessions with clean handoffs are the goal.

### Durability note

This doc is intentionally written in **tier language** (flagship /
standard / lightweight) rather than specific model versions. Anthropic
ships new models regularly; what matters is the *relative* cost and
capability of each tier. Where specific names or numbers appear, they're
marked *"as of writing"* — re-audit them yearly. See
[§ Appendix: what to re-audit](#appendix-things-that-rot--re-audit-yearly)
for the short list.

---

## 1. Session discipline — when to start a new session

This is the single biggest lever. **Start a new Claude Code session when
any of these signals fire:**

| Signal | Why |
|--------|-----|
| Topic has pivoted 2+ times (e.g. feature work → bug fix → docs) | Stale earlier context is re-read on every message for no benefit |
| You've used ≥60% of your 5-hour token window in <2 hours | The session is burning tokens on re-reads faster than producing output |
| Claude is re-reading a file it already edited this session | Context got compacted and dropped critical state — the session is already degraded |
| Claude's responses are getting slower, shorter, or more generic | Context bloat is crowding out reasoning |
| Context has passed the context budget (below) | Every further turn re-reads all of it |
| You're about to start a completely unrelated feature | Everything from earlier is dead weight |
| A full `/compact` no longer restores responsiveness | Signal is lost in the summary; start fresh |

### Context budget

The main session's context, not subagents, is usually the dominant cost:
every turn re-reads all of it, and a long-lived session spends most of
its turns at a large context. **Keep a context budget** (the core
plugin's `context_budget` option, default 250k tokens). When the main
context passes it:

1. Write the handoff (current state, decisions made, the next step) to
   the project's memory or handoff file.
2. Then ask the user to `/compact`, or to start a fresh session with the
   handoff.

The `context_watch` hook says so when the budget is crossed, and again
every further 100k. Writing the handoff first is what makes `/compact`
safe at any point: the state you need is on disk, not only in the
conversation.

**Compaction count.** Auto-compaction is allowed to happen, and one
compaction is not a problem. Each one drops detail, though, so the same
hook counts them (`compact_boundary` entries in the transcript). When a
session has been compacted automatically `max_compactions` times
(default 2; 0 turns it off), it asks the agent to write the session
handoff (`session-close`). The user's own `/compact` runs count against
`manual_compactions` (default 1): while some are left the user is told
they can still `/compact`; after that a fresh session started from the
handoff is recommended, and to do the same whenever the agent seems to be
forgetting things. It speaks once per count.

### Handoff protocol between sessions

At the end of every substantive session, create a **session note** so the
next session doesn't waste tokens re-discovering context:

```
> Write a session-notes.md in the repo root (or docs/session-notes/).
> Capture: what changed, what's still in progress, which files matter,
> which decisions are final vs open. One page max.
```

Start the next session with:

```
Read session-notes.md first. Continue from the "in progress" item.
```

### Use `/clear` and `/compact` as tactical tools

- `/clear` — between unrelated tasks in the same session. Cheaper than
  carrying dead context forward.
- `/compact` — at natural breakpoints (after planning, after finishing a
  subtask, before switching focus), or when the context budget is
  crossed. Mid-implementation or mid-debug, write the handoff first so
  the state you need survives the summary.
- `/cost` — check spend mid-session. If you've burned 50%+ of your window
  and haven't shipped much, stop and start fresh.

---

## 2. Make each message cheaper

### 2.1 Edit messages instead of follow-ups
When Claude misses, **edit the original message** rather than typing
"Actually I meant X". A follow-up stacks on the full history; editing
replaces the old exchange and regenerates. This is the single
highest-ROI hack. Use it constantly.

### 2.2 Target specific sections — never ask for a full redo
"The entire report is wrong, redo it" regenerates every token.
"Section 3 is wrong because X — rewrite only section 3, keep the rest"
regenerates only what's broken.

Add to prompts when you know exactly what you want:

> No commentary. No explanations. Just the output.

Claude's default is verbose and helpful. Every "Happy to help! Here's
what I did..." is tokens you paid for.

### 2.3 Batch independent tasks into one message
Three prompts = three full context reloads.
One prompt with three asks = one reload.

Bad:
```
Summarize this article.
List the main points.
Suggest a headline.
```

Good:
```
Summarize this article, list the main points, and suggest a headline.
```

Bonus: the answer is usually better because Claude sees the full scope
at once.

### 2.4 Keep a stable prompt structure
Repeated near-identical prompts get partial prompt caching. Use the same
template for recurring work; only swap the variable part.

Reusable template:

> I want to [task] to [success criteria]. Read [specific file or folder].
> Ask me questions before you start if anything is ambiguous.

### 2.5 Let the model ask you clarifying questions
A 500-word prompt costs 500 tokens every single re-read. A 15-word prompt
plus a few clarifying question→click-answer cycles is cheaper **and**
produces better output. Invite questions instead of pre-loading context.

### 2.6 Voice input (Wispr / built-in dictation) → richer one-shot context
Typed prompts are lazy ("make it better"). Spoken prompts naturally pack
more context into fewer messages ("the tone is too stiff — make it sound
like I'm texting a friend who runs a 200-person company; keep the data
but casual; only redo section 2"). Fewer messages = fewer reloads.

---

## 3. Prevent context bloat

### 3.1 Keep `CLAUDE.md` lean
Every word in `CLAUDE.md` is read **at session start, every session**.
Aim for <300 lines. Anthropic's own warning: **bloated CLAUDE.md files
make Claude ignore your actual instructions.**

What belongs in `CLAUDE.md`:
- Non-obvious, session-critical rules ("don't push to main", "use
  library A, not library B", "platform scope is X+Y only")
- Architecture decisions the agent will otherwise re-derive every session
- Pointers to where detailed docs live (don't inline them)

What does **not** belong:
- Tutorials
- Descriptions of what the app does ("MyApp is a…")
- Historical context
- Aspirational / "we should someday…" notes
- Anything the agent already knows from the code itself

If a rule doesn't change the agent's behavior, delete it.

### 3.2 Move rare instructions to Skills
`CLAUDE.md` loads every session. **Skills** load on demand. If a
workflow is used 10% of the time, put it in a Skill, not CLAUDE.md.

### 3.3 Use `.claudeignore`
Works like `.gitignore`. Exclude from context:
- `node_modules/`, `build/`, `dist/`
- Generated code (`*.g.dart`, `*.freezed.dart`, `*.pb.go`)
- Large assets (images, video, binary blobs)
- Lock files (`*.lock`, `pubspec.lock`, `package-lock.json`)
- Generated migrations
- Test fixtures >100 lines

This is one of the highest-leverage single moves.

### 3.4 Be specific about targets
"Look at the codebase and figure out where X lives" → Claude reads
several files, burns thousands of tokens.
"Read `lib/services/foo.dart:50–80`" → one targeted read, hundreds of
tokens.

Always point Claude at the target. Never make it search.

### 3.5 Don't dump whole folders into a session
For repo-aware sessions, scope the entry prompt:

> Work only in `src/ui/settings/`. Don't open files outside that
> directory unless I explicitly ask.

Every file opened "just in case" is tokens you'll pay for on every
subsequent re-read.

### 3.6 Limit MCP servers
Keep ≤10 enabled per project. Each server's tool list is injected at
startup. Run `/mcp` to audit. Disable anything you don't use on this
project.

### 3.7 Turn off features you're not using
Web search, connectors, Extended Thinking — all add tokens to every
response.

- Default: everything off.
- Turn features on per-task, not per-account.
- Extended Thinking is worth it for architecture / debugging; overkill
  for a rename.

---

## 4. Convert files before you attach them

Binary and image formats cost **orders of magnitude more tokens** than
their plain-text equivalent:

- A PDF page costs **thousands of tokens** each (exact count depends on
  the current tokenizer and whether vision is invoked).
- A full-size screenshot costs roughly an order of magnitude more than
  the same information as plain text.
- DOCX / PPTX carry invisible metadata bloat.

The cheapest form is almost always **clean Markdown / plain text**.
Before attaching to a Claude Code session or pasting as context:

1. Extract the text with the OS clipboard, `pdftotext`, or a quick
   Google Doc round-trip (`doc.new` → paste → Download as `.md`).
2. Crop screenshots **tight** to just the relevant region — or OCR it
   and paste the text. A tight crop can be an order of magnitude
   cheaper; OCR'd text is usually cheaper still.
3. Save the result as `.md` or `.txt` in the repo.

If you attach the same multi-page PDF to several chats, you've burned
that document's token cost *per chat*. Convert once, reference forever.

For recurring documents, put the clean text version in the repo under
`docs/` so every session references the same file instead of
re-uploading.

---

## 5. Match the tool to the task

### 5.1 Model tiers

Anthropic ships models in three tiers. Names change; tiers don't.
Switch tiers with `/model` based on task complexity — never pay
flagship prices for lightweight work.

| Tier | Use for | Currently (as of writing, Claude 4.x era) |
|------|---------|-------------------------------------------|
| **Flagship** | Architecture, ambiguous decisions, cross-file refactors, security-critical changes, synthesis of multiple subagent outputs | Opus |
| **Standard** | Most coding work, routine debugging, standard feature implementation | Sonnet |
| **Lightweight** | Mechanical work: renames, scaffolding, boilerplate generation, implementing a stubbed function with a defined contract, rote refactors | Haiku |

**Check Anthropic's current model list before hardcoding a name** —
`docs.anthropic.com/en/docs/about-claude/models` is the source of
truth. The tier relationships (standard is substantially cheaper than
flagship; lightweight is substantially cheaper than standard) are
stable; the specific version suffixes are not.

Rule of thumb: **if the task takes the model <30 seconds of "thinking"
to answer, it probably doesn't need the flagship tier.**

### 5.2 Recommended `~/.claude/settings.json`

```jsonc
{
  // Default to the standard tier for most work.
  "model": "<current standard-tier model, e.g. 'sonnet'>",
  "env": {
    // Cap thinking budget well below Anthropic's current default —
    // measure in your own workflow. A lower cap cuts hidden cost
    // meaningfully at minimal quality loss on most tasks.
    "MAX_THINKING_TOKENS": "<your cap, e.g. 10000>",

    // Have subagents default to the lightweight tier. Override
    // per-call when a task genuinely needs more power.
    "CLAUDE_CODE_SUBAGENT_MODEL": "<current lightweight-tier model, e.g. 'haiku'>"
  }
}
```

Why these settings matter (tier-relative, not version-specific):

- Capping `MAX_THINKING_TOKENS` below Anthropic's default cuts **hidden
  thinking cost** — extended-thinking tokens aren't visible in the
  conversation but they *are* billed. The exact default changes over
  time; always cap below whatever it currently is.
- Routing subagents to the lightweight tier captures the highest-leverage
  delegation win. Most subagent work (file reading, search, boilerplate)
  doesn't need the standard or flagship tier.

### 5.3 When to delegate to a subagent
A subagent saves tokens only when the context it keeps out of the main
session, multiplied by the main-session turns still to come, is larger
than what the subagent itself spends. Measure this on your own
transcripts (the `effectiveness` skill does) before tuning; the shape of
the rule holds across setups:

- **Delegate when the investigation is large and the answer is small,
  and plenty of the session remains.** Reading many files to return a
  short conclusion is the case that pays.
- **Do not delegate the last step of a task or a session.** With few
  main-session turns left, there is nothing for the kept-out context to
  save. Do it inline.
- **Brief bounded jobs.** State the scope and a stop-and-report point
  ("report back if this grows well past N tool calls"). A very long
  subagent re-reads its own growing context and loses what it saved;
  split a big job into independent briefs instead.
- **Prefer read-only specialist agents for searching and reading**
  (Explore, documentation lookups, web fetch) over a general-purpose
  agent. They carry less and stay on task.
- **Ask for a short report, with the detail in files.** The report lands
  in the main context and is re-read every turn after it.
- **Legitimate non-token reasons, stated when used:** an independent
  fresh-eyes review that should not be anchored on your reading,
  parallel independent work, and isolation for risky operations. Say
  which one applies; it is not a token saving.

The core plugin's `delegation_check` hook injects this checklist the
first time a session spawns a subagent.

### 5.4 Never delegate *understanding*
Bad: "Based on your findings, fix the bug."
Good: "In `lib/services/foo.dart` line 82, change the null check from
`!= null` to `?.isNotEmpty == true`."

Synthesize first. Delegate concrete work second.

---

## 6. Tool-specific habits for Claude Code

### 6.1 Give Claude Code a tight scope before it starts
Claude Code tends to explore — it reads directories, opens sibling
files, runs tree checks. If you don't scope it, it will investigate
everything in sight.

```
Create a bar chart from data.csv showing monthly revenue for 2025.
Save it as chart.png. Don't explore other files.
```

### 6.2 Use Projects (repos) + `CLAUDE.md` for recurring context
Uploading the same PDF to five chats = five full re-tokenizations. A
repo with a clean `CLAUDE.md` + a `docs/` folder = one canonical copy
every session references.

### 6.3 Scheduled / recurring tasks
For weekly reports, digests, recurring research — use a scheduled task
or a script the agent runs. Don't do it manually in a growing session.

### 6.4 Turn off Memory; use User Preferences / Styles
- Disable Memory. It's unpredictable and the cached context leaks
  into unrelated work.
- Set User Preferences once (writing style, preferred paragraph length,
  default language) — these persist without eating your context window.

### 6.5 Spread work across the 5-hour rolling window
Claude usage is a rolling 5-hour window. If you burn your cap in one
morning session, most of your day's capacity goes unused. Split into
2–3 sessions: morning / afternoon / evening. Each session starts
fresh, which also forces better session discipline.

### 6.6 Habits for long feedback-driven sessions

Long sessions that go through several rounds of feedback across many
files tend to end near the context ceiling. Each habit below removes a
common source of wasted tokens in that kind of session.

**Don't read a file the user just said to delete or disable.**
If the user says "remove that, it's broken, try again", grep just
enough to find the call site and write the replacement — never
fully `Read` the doomed file. Reading a large file that is about
to be deleted spends tens of kilobytes of context that is never used.

**Filter command noise at the source.** `flutter analyze`,
`flutter pub get`, `npm install`, `cargo build` all emit screens
of dependency-resolver chatter. Always pipe through
`| grep -E "error|warning|issues found" | tail -N` (or whatever
filter matches the tool). Each unfiltered run burns hundreds of
chars of "package X.Y.Z+1 available" noise nobody reads.

**One analyze / typecheck pass per logical work unit, not per
edit.** Build confidence via type-correctness reasoning + the
Edit tool's own success/failure feedback. Run the validator once
right before commit, not five times across the session.

**Trust prior reads in the same session.** If you read
`example_service.py` lines 100-200 in round 3, don't re-Read
the same range in round 5 — work from memory or use Grep for the
specific line you need. The harness tracks file state for you;
re-reading is duplicate context.

**Fix-report length scales with fix complexity.** A one-line
code change → one paragraph in the existing same-day fix report.
A new feature or architectural change → its own file. A round-up
covering 4 unrelated bug fixes → group under ONE short report,
not four full ones with repeated symptom/root-cause/fix
boilerplate.

**Commit messages: subject + 5-10 line body covers most work.**
Reserve longer bodies for architectural changes the next reader
can't reconstruct from the diff. Don't restate the fix-report
content — link to it. The git log should be scannable, not a
duplicate of `docs/fix_reports/`.

**End-of-message summaries: cap at 100 words OR one small table
— not both.** If the work matched the user's request, they don't
need a 40-line recap. Default to one tight paragraph; expand
only if the user is debugging or about to test.

**Acknowledge system-reminder noise silently.** Reminders like
"TodoWrite hasn't been used recently" are operational nudges,
not user content. Update the todo list (or don't, if not
warranted) and keep moving. Never narrate the reminder back to
the user — it adds noise without value.

**Restore noisy auto-modified files (`pubspec.lock`,
`.flutter-plugins-dependencies`) before commit** when the
modification was just `flutter pub get` churn from a non-pinned
local toolchain. The diff will be huge and meaningless to
reviewers.

**Quick-table over prose for batched feedback responses.** When
the user lands a multi-item feedback batch, lead with a triage
table (item / category / where it lands) before any code action.
Prose triage takes 3× the words for the same information.

---

## 7. Checklist + documentation discipline

### 7.0 Stream-timeout hygiene (mandatory — read first)

The harness kills the stream with `API Error: Stream idle timeout —
partial response received` when a single tool payload, or a silent
generation gap between tool calls, sits idle too long. **Every
long-running task must be split up ahead of time** — this is not
optional:

1. **Single `Write` payloads ≤ 200 lines / ≤ 8 KB. Hard cap.** If a
   new file is bigger, write a skeleton first, then `Edit`-append
   the body in 2–3 smaller chunks.
2. **Never call `Read` on a file > 25 KB without `offset` + `limit`.**
   Grep to locate the section first, then `Read` the range you need.
3. **Never batch multiple `Read`s > 10 KB in the same tool-call
   message.** One big `Read` per message, max.
4. **Prefer `Edit` over `Write` for existing files.** Edits send only
   the diff; rewrites send the whole file.
5. **Inter-tool text ≤ 25 words.** Narrating deliberation between
   tool calls is silent stream time and a top timeout cause.
6. **If a stream dies mid-call, NEVER retry at the same size.**
   Split the payload in half and resume with smaller steps. Retrying
   at the same size usually dies at the same point.
7. **Multi-file tasks must be chunked.** If a refactor touches more
   than ~5 files, commit-and-push after each logical sub-step rather
   than holding 20 files in a pending diff.

Symptom-to-fix cheat sheet:

| Symptom | Most likely cause | Fix |
|---|---|---|
| Timeout during `Write` | File payload too big in one call | Skeleton + `Edit` appends |
| Timeout during `Read` | File > 25 KB or batched with others | `Grep` + `Read` with `offset`/`limit` |
| Timeout between tools | Long silent generation | Shorter text between calls |
| Timeout on retry at same size | Payload genuinely too large | Halve the work, don't retry |

This rule takes precedence over stylistic preferences. A task that
needs to be split is split, even if that means more commits or more
tool calls.

### 7.1 Checklist first, code second
Before writing any code in a session:

1. Restate every request from the user's latest message as a checklist.
2. One line per concrete ask. Split compound sentences.
3. If the request depends on a prior session's summary, fold those open
   items into the same list.

### 7.2 Process or park — never drop
Every checklist item ends the session as one of:
- `[x]` Done
- `[~]` In progress (with a TODO follow-up entry)
- Explicitly deferred to `TODOS.md` / `FEATURE_BACKLOG.md` with a
  one-line justification

Never "forget" an item mid-session.

### 7.3 Ask before continuing
If the checklist wasn't fully processed, finish the session summary
with:

> Would you like me to continue with the next steps before you review
> the new additions?

Do not silently pick up pending items in the next turn.

### 7.4 Write to the right doc
- **Bug fix** → `docs/fix_reports/YYYY-MM-DD-<slug>.md` (symptom, root
  cause, fix, rollback). Add a CHANGELOG line **only** if the bug was
  user-visible in a shipped build.
- **User-facing feature** → `docs/features/<slug>.md` (trigger, happy
  path, failure modes, preferences created). One CHANGELOG bullet.
- **Internal feature / plumbing** → commit body only. **No CHANGELOG
  entry.**
- **Refactor / cleanup / CI / docs** → commit body only. **No
  CHANGELOG entry.**

The changelog is user-facing. Flooding it with fix-chatter trains users
to skip release notes entirely.

### 7.5 Sweep per owner message, not per session
When an owner message contains a decision, a deferral, or a change of
scope, route it to its tracking file *in that turn's work* — never batch
records for session close. A session that dies mid-flight should leave
the tracking files already true.

### 7.6 Follow-ups must be written, not just said
Any sentence in your own output that amounts to a future action —
*"when you implement X, remember…"*, *"re-run the scan after…"* — MUST
also land in a tracking file (TODO, the relevant proposal/skill) in the
same turn. Chat memory does not survive the session window; a follow-up
that lives only in chat is a silent drop. Before signing off, re-read
your last message for imperative verbs aimed at the future and confirm
each one has a written home.

### 7.7 "Already shipped?" pre-flight
Before implementing any item that names a bug ID, a TODO line, or a
proposal phase, grep git history first — multi-session work routinely
produces commits not yet reflected in the tracking files:

```bash
git log --all --oneline --grep='<keyword>'
git log --all --oneline -- <expected file or dir>
```

If it shipped: verify it matches the spec, flip the tracking line, move
on. If it partially shipped, narrow the checklist to the remainder.

### 7.8 Docs-first for undocumented work
If the answer to "where in the repo does the *intent* of this work
live?" is *nowhere yet*, write the design/fix doc before the code, in
the same branch — ideally a preceding commit. Docs written after the
code rationalize what was built; docs written before anchor what should
be built, and they surface the edge cases before an implementation
that's hard to walk back. Exceptions (commit body is enough): one-line
fixes, no-behavior dependency bumps, test-only additions to a
documented surface.

### 7.9 Proactive completion
If a tool can finish the step (run the server, open the PR, deploy the
workflow, run the check), do it — don't file an owner-action TODO for
work the agent can do. Ask first only for destructive, paid,
contract-changing, or new-dependency steps. An owner round-trip costs
more than any tool call.

---

## 8. Verification — trust but check

Subagents report **intent**, not reality. Before marking any delegated
task complete:

- `git diff` after any subagent that wrote code. Confirm the diff
  matches the plan.
- Spot-read one or two lines the subagent claimed to change.
- Run the linter / analyzer yourself — never rely on a subagent's
  "it compiles" statement.
- For UI changes: run the app, exercise the feature. Type checking
  verifies code correctness, not feature correctness.

---

## 9. Know what Claude is bad at

Stop burning tokens on tasks Claude wasn't built for. The *categories*
below are durable; the specific tool suggestions are examples as of
writing — verify current options before committing to a workflow.

| Category Claude is weak at | Use instead (examples, re-audit yearly) |
|----------------------------|-----------------------------------------|
| Image generation | A dedicated image model (Midjourney, DALL-E-class, Gemini image, etc.) |
| Real-time / breaking-news web search | A live-search-first assistant (Grok, ChatGPT with search, Perplexity, etc.) |
| Long-form audio/video transcription | Dedicated STT (Whisper, AssemblyAI, Deepgram) — pass the transcript to Claude |
| Bulk OCR on scanned PDFs | OCR first (Tesseract, AWS Textract), then pass text to Claude |
| Heavy numerical simulation | A real compute environment — Claude is not a CAS |

Five messages of "describe the image you want" to work around a
capability gap = five messages of wasted tokens. Pick the right tool up
front.

---

## 10. Pre-commit self-check (run at every boundary)

Before committing OR ending a session, the agent should self-check:

1. **Did I follow the checklist?** Every item processed or parked?
2. **Am I writing to the right doc?** Fix → fix report; feature →
   feature doc; plumbing → commit body only.
3. **Is the CHANGELOG entry genuinely user-visible?** If not, delete it.
4. **Did I re-read anything I already had in context?** If yes, that's
   a signal I should have used Grep or memory instead.
5. **If I delegated work, did I `git diff` and verify?**
6. **Has the session pivoted enough that I should recommend starting
   fresh next turn?** If yes, say so in the summary.

---

## 11. Audit the static footprint, not just the conversation

§1–§10 are about keeping the *conversation* cheap. This section is
about the **always-on overhead** — the tokens that load every session
before you type a word: `CLAUDE.md`, every skill description, every
MCP tool schema, every agent description. (Adapted from
affaan-m/everything-claude-code `context-budget` + `strategic-compact`,
kept as a habit rather than a script.)

### 11.1 What costs what (the levers, biggest first)

- **MCP tool schemas are the biggest lever — ~500 tokens per tool.**
  A 30-tool server can cost more than every skill in a project combined,
  so a few connected servers easily become the largest slice of static
  overhead. Don't add a server to wrap a CLI you already have.
- **Skill *descriptions* load every session; skill *bodies* don't.**
  Only the `SKILL.md` frontmatter `description` is always present —
  the body loads on activation. So a long, precise description is a
  feature (better routing); a long body is free until used. Keep
  descriptions specific but not padded.
- **`CLAUDE.md` loads in full, every message.** It's the one file
  where every line is rent. §3.1 already says keep it lean; this is
  the measurement reason why.
- **Agent descriptions load into every Task invocation** even for
  agents you never spawn. If a project adds a `.claude/agents/` dir,
  this is why the descriptions must stay short.

### 11.2 The audit (run after adding any skill / MCP server / agent)

1. Estimate: prose ≈ `words × 1.3`, code ≈ `chars / 4`.
2. Flag: any `SKILL.md` body >400 lines (split into `references/`,
   as a security-review skill might), any skill description >40 words of
   padding, any MCP server >20 tools, `CLAUDE.md` growth that should
   have been a skill instead (the "fix goes in the skill" rule).
3. Bucket each component: **always needed** (referenced in CLAUDE.md /
   matches the project's stack) · **sometimes** (domain-specific, loads on
   trigger — fine) · **rarely** (overlaps another skill → merge, or no
   trigger ever fires → cut).
4. The biggest realistic win is almost always **MCP tools**, not
   skills. Audit there first.

### 11.3 Strategic compaction (`/compact` at phase boundaries)

Auto-compaction fires at an arbitrary token count — often mid-edit,
losing file paths and variable names. Drive it manually at *logical*
boundaries instead. This is the in-session cousin of §1's
start-a-fresh-session rule and §0c's pacing checkpoint.

| Phase transition | `/compact`? | Why |
|---|---|---|
| Research / Explore → planning | Yes | The bulky reads are spent; the plan is the distilled output. |
| Planning → implementation | Yes | Plan is in TodoWrite / a doc; free the window for code. |
| Debugging → next feature | Yes | Dead-end traces pollute unrelated work. |
| After a failed approach | Yes | Clear the wrong path before the next attempt. |
| Implementation → testing | Maybe | Keep if tests reference just-written code. |
| **Mid-implementation** | **No** | Losing live file paths + partial state is the costly case. |

Survives a compact: `CLAUDE.md`, the TodoWrite list, files on disk,
git state. Lost: intermediate reasoning, file contents you read,
verbally-stated preferences. So before compacting, **write anything
load-bearing to a tracking file**
and use a summary: `/compact focus on <the next step>`.

**The carry-over hook.** Compaction, manual or automatic, is lossy and
silent: the summary routinely drops open PR numbers, the ids of
background tasks and subagents still running, the question waiting on
the user, and which files were being edited. Gnomonkit core's
`compact_carryover.py` covers that gap without a model call:

- **PreCompact** reads the transcript from the last compaction on and
  writes `~/.gnomonkit/carryover/<session>.json`: PR and issue numbers
  and URLs (from PR links, `gh pr`/`gh issue` commands and GitHub URLs),
  background Bash tasks and async subagents launched with no completion
  notification yet, files written or edited, the last user message,
  questions asked after it, worktrees in use, and the handoff file if
  the session wrote one. Running items from the previous record carry
  forward until their notification arrives.
- **SessionStart (`compact`)** injects that record as a short list,
  capped at 3,500 characters, headed "Carried over from before
  compaction".

The record is extracted, not understood: an item can be stale (the task
finished during the compaction, the PR was merged, the question was
settled in a message the extractor did not count). **Verify each item
before acting on it**: read the task's output, `gh pr view` the PR, read
the handoff file. It is a safety net under the handoff, not a
replacement for it. `GNOMONKIT_CARRYOVER=off` turns it off.

---

## Where to start if you're hitting limits today

Pick **three habits this week.** You'll feel the difference in 48
hours.

- **If you use Claude Code daily**: §1 (session discipline), §2.1 (edit
  instead of follow-up), §3.1 (trim `CLAUDE.md`).
- **If you chat more than you code**: §2.2 (target sections, no full
  redos), §2.3 (batch tasks), §4 (convert files before attaching).
- **If you keep hitting your 5-hour cap**: §5.1 (right tier per
  task), §5.3 (delegate to lightweight-tier subagents), §6.5 (spread
  work across the day).

Don't try all 10 sections at once. Stack habits one at a time.

---

## Appendix: Things that rot — re-audit yearly

The following items in this doc are expected to drift as Anthropic
ships new models and tokenizers. When you re-audit, check each one
against the current source of truth and update the **example** values
— the surrounding rules should stay unchanged.

| What | Where to check |
|------|----------------|
| Model names per tier (flagship / standard / lightweight) | Anthropic docs → models page |
| `MAX_THINKING_TOKENS` default | Anthropic docs → extended thinking reference |
| Relative cost ratios between tiers | Anthropic pricing page |
| Tokenizer counts for PDF / image / markdown | Anthropic docs → tokens reference, or run a count yourself |
| 5-hour rolling usage window behavior | Claude Code usage-limits docs |
| `/model`, `/clear`, `/compact`, `/cost`, `/mcp` command names | `claude --help` or Claude Code release notes |
| Competitor tool names in §9 | Verify each tool still exists and is still best-in-category |
| `~/.claude/settings.json` keys | Claude Code release notes — key names occasionally change |

**Rule**: if anything in this doc references a specific version,
number, or competitor name, that line is a re-audit candidate. The
tier-level rules (delegate cheap work to cheaper tiers, keep context
small, edit don't append) are permanent.
