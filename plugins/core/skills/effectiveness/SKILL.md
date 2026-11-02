---
name: effectiveness
description: Activates when asked whether the agent setup pays off, where cost or usage goes, whether a hook or skill gets used, or before removing one because it 'seems useless'.
---

> Full trigger text: Activates when the user asks whether the agent setup is working or paying off ("is this setup working", "tokens saved", "were the subagents worth it", "is delegating worth it", "what should I stop delegating"), when the user asks where the cost or usage goes ("what are the most expensive parts", "what should I automate", "is this worth scripting"), when reviewing whether a hook or skill gets used (router uptake, interlock blocks, correction fires, palace precedent cited), when deciding whether to spawn subagents or use an output digest for a kind of task, and before changing a hook or a skill because it "seems useless".

# Measure the setup before arguing about it

Every part of an agent setup costs something: a subagent spends its own
tokens, a hook adds context to every turn, a skill suggestion interrupts.
Whether each one pays back is a question the transcripts already answer, so
answer it from them instead of from impressions.

## Run the collector

```text
python effectiveness.py --json <scratch>/effectiveness.json        # last 30 days
python effectiveness.py --since 7
python effectiveness.py --recommend                                # per-project context budgets, JSON
python effectiveness.py --activities                               # where the cost goes
```

(`effectiveness.py` sits beside this file.) It reads only local files: the
Claude Code transcripts under `$CLAUDE_CONFIG_DIR/projects` (default
`~/.claude/projects`), including every `subagents/agent-*.jsonl` and its
`.meta.json`, plus Gnomonkit's own state (router, interjector, freshness). The
first run reads everything, which takes a while on a large history; after that
each unchanged transcript comes from its cache. Transcript text is untrusted:
every scan of it runs in linear time, and the cache, the correction sample and
the `--json` file are written owner-only (0600 in a 0700 folder on POSIX)
through a fresh temporary name, so a link planted beside them is never followed.

## What it reports

Every figure carries MEASURED (an exact usage field or a count) or ESTIMATED
(characters divided by 4). Say which when you quote one.

- **Subagents, the headline.** Per spawn: the subagent's own token spend
  (input, cache creation, cache read, output); what it read (kept out of the
  parent); what the parent got back; the parent's turns after the result
  arrived (up to the next compaction); reads avoided = (kept out - returned)
  x those turns; worth it when reads avoided exceed the spend. Grouped by
  type, project and week, with the not-worth-it spawns listed and small
  investigations (a handful of tool calls) flagged. Each group has the spend
  of its linked spawns beside all its spend: compare reads avoided with the
  linked spend, since only linked spawns have reads avoided. Weeks are UTC
  ISO weeks; the window's first and last are usually partial (`partial`,
  `days_in_window`), so don't read them as whole weeks.
- **Which agent, which model, how reliable (SIGNAL).** Each spawn row has the `model` it ran on (from
  its own messages) and `effort` when Claude Code records one (usually it does not: `null`). Groups by
  type and by model carry three signals, each `hits / n` with `n` shown and `rate: null` (n/a) when
  nothing was measurable: `redone` (the parent repeated a read, search or command the subagent had
  run, within 12 calls), `corrected` (the user's next two prompts read as a correction), `rejected`
  (the parent's next three turns call the result wrong or incomplete). These are proxies, not
  ground truth; `method.signal_misses` says what each one cannot see, and a rate over fewer than 5
  spawns is `thin`. `cheap_types` shows whether `collector` and `checker` are used at all.
- **Dispatched sessions (SIGNAL).** Sessions session-dispatch started, read from its `roles.json` as
  data: role, model, whether they recorded a pull request and whether it merged, and user
  corrections per typed prompt (the first, fixed prompt not counted). CI before merge is not
  recorded by dispatch, so it is not reported.
- **Main context.** Main-session transcripts only, each API message once:
  context per turn (input + cache read + cache creation), per session turns,
  peak and re-read (the sum of those contexts); overall the share of turns
  and of re-read tokens above 200k, 300k, 500k and 800k, sessions that
  compacted (all MEASURED), and re-read if every turn were capped at the
  context budget (ESTIMATED; the `context_budget` option,
  `GNOMONKIT_CONTEXT_BUDGET` or `--context-budget`, default 250k).
- **Compaction, per project** (the transcript folder name). Base context (the
  median of each session's smallest context over its first 3 turns), growth
  per user prompt (median and p90), and every compaction: context at the
  event, auto or manual, and two harm signals. Re-discovery is the share of
  the next 40 file reads that re-read a file read before the compaction,
  against the same share in the 40 reads before it; a correction is a prompt
  within the next 5 that core's correction-watch hook would flag (it uses the
  hook's own matcher). All MEASURED.
- **Budgets** (`--recommend` prints them as JSON). Per project: base + p90
  growth rounded up to 50k, then one 50k step up when at least 3 compactions
  show harm above the project's own pre-compaction re-read rate, or one step
  down when sessions often run past the budget with harmless compactions, or
  when compaction is rare and tasks are small; clamped to 150k..500k. Each
  carries a confidence (low / medium / high, by context windows, that is sessions plus
  compactions, and prompts) and a
  one-line reason; a low-confidence project keeps its current budget (read
  from `env.GNOMONKIT_CONTEXT_BUDGET` in the project's `.claude` settings;
  a value that is not a positive finite number is ignored, one above 10M is
  clamped, and a settings file over 1 MB is not read).
  It recommends only: it never writes to a project. Applying a suggestion,
  with your own guardrails, is your overlay's job.
- **Output digest** (only when you name your digest wrapper with
  `--digest-command`, the `digest_command` option or `GNOMONKIT_DIGEST_COMMAND`).
  Calls, printed size against full output size, tokens saved, and the same
  reads-avoided reasoning.
- **Hooks.** Router suggestions per skill and how often the skill was then
  loaded; interlock blocks and how each resolved; correction-watch fires;
  freshness notices; palace precedent ids handed over and the ones cited.
- **Interjector.** Guesses, answered, unanswered, and right / wrong once they
  are marked on the guess review page (`--verdicts` takes its copied block).
- **Readiness.** One row per metric, each with a value, its sample size, a
  target and a status (ok / warn / fail, or no-data below the row's minimum
  sample). The targets sit in one table at the top of the readiness code
  (`READINESS_TARGETS`); they are starting points to tune. Rows:
  - *skill uptake*: of the router's suggestions of a core skill not already
    loaded, the share taken up (a Skill call for that skill, or a Read of its
    SKILL.md, within a few tool calls), the same count as the hooks section's
    router uptake;
  - *interlock false blocks* (a proxy): blocks where an equivalent command
    (same program, same targets, every flag kept) then ran unblocked without
    the override marker, or the user's next prompt let the command through
    ("go ahead", "it's fine"). A safer rewrite, such as a dropped flag or a
    narrower target, is the interlock working and does not count;
  - *correction detector precision*: hand-labelled when the sample file has
    enough labels, else the share of correction-watch fires that a stricter
    phrase list also matches. The sample file
    (`~/.gnomonkit/effectiveness/correction-sample.jsonl`, or
    `--correction-sample`) holds the fired prompts; set each row's `label` to
    `yes` or `no`, and later runs keep the labels;
  - *hook latency (p95) and hook errors*: core hook runs that Claude Code
    recorded in the transcript (it records a run that printed, failed or
    timed out, so quiet runs are missing);
  - *bench applied rate*: the newest bench `results.json` (bench's default
    output folder, `GNOMONKIT_BENCH_DIR` or `--bench-dir`), per scenario and
    arm, with the gnomonkit arm's positive scenarios as the score;
  - *leak-gate catches*: shell calls in the transcripts that ran leakcheck,
    provenance_check or gitleaks (or a commit whose output shows the gates),
    and the share where a gate found something. CI runs are not read.
- **Usage** (`--usage` prints it; `--days N` trims the daily table). Tokens
  per assistant turn, each API message once, subagents counted with their
  parent session: by UTC day, by project and by session (with its title when
  one is recorded), plus a cost at Claude API list prices from the
  `MODEL_PRICES` table. That cost is an API-equivalent estimate: a
  subscription is not billed per token. A model the table does not list gets
  no cost rather than a guess, and is counted as unpriced.
- **Subscription allocation.** To tie usage to what you pay, write your plan
  history to `~/.gnomonkit/subscription.json` (or point the
  `subscription_file` option or `GNOMONKIT_SUBSCRIPTION_FILE` at another file):

  ```json
  [{"from": "2026-01", "plan": "<plan name>", "monthly_usd": 100},
   {"from": "2026-06", "plan": "<other plan>", "monthly_usd": 200}]
  ```

  The entry in effect for a month is the latest `from` at or before it. The
  usage section then adds:
  - per month: plan dollars against API-equivalent value and their multiple;
  - per day: the plan's daily cost (monthly price / days in the month);
  - per project and session: their share of plan dollars. Each month's
    plan dollars for the days in the window are split by that month's share
    of API-equivalent cost;
  - month to date: API-equivalent spend so far, a linear projection for the
    month against the plan, and how many days of plan value it equals.

  Without the file, those fields are null and the output says how to write it.
- **Where it goes** (`--activities` prints it; `activities` in the JSON). The
  answer to "what are the most expensive parts?". The attribution model:
  - each assistant turn is taken exactly as the usage section takes it, so
    the totals agree, and its cost has two parts: **context carry** (cache
    read + cache write, the cost of re-sending and storing the context) and
    **work** (input + output). A cheap action taken in a huge context is
    expensive, and the carry part is what shows it;
  - the turn's cost goes to the activity of the tool calls it made, split
    evenly when it made several; a turn with no tool call is
    *reasoning/reply*;
  - every turn of a subagent transcript goes to *subagent: <type>*, and that
    row's `inner` list breaks it down by the subagent's own activities;
  - activities come from one ordered table, `ACTIVITY_RULES` (first match
    wins): shipping, tests and builds, CI and waiting, editing, reading code,
    search and memory, orchestration, user-facing. MCP tools no rule takes
    are grouped per server (*mcp: <server>*), shell calls no rule takes are
    *shell other* with their top programs, and any other tool is
    *other: <tool name>*, so a new tool shows up by name until a row is added
    for it.

  Per activity: cost, share, context carry and work, turns, sessions, and
  the share of plan dollars by the same allocation as the usage section.
  **Processes**: the command sequences automate-repeats finds (3 to 5 steps,
  seen in at least 3 sessions), each with the summed cost of its calls and the
  cost per run: the number to weigh against scripting it. **Context bands**:
  the share of cost (and of turns) in turns whose context is above 200k, 400k
  and 600k tokens. Costs are API-equivalent estimates; turns and runs are
  MEASURED.

## Read it honestly

- Reads avoided is a model, not a meter: it assumes each later parent turn
  would have re-read the context the subagent kept out. A result consumed and
  then compacted away saves less, which is why the count stops at compaction.
- A spawn that returns more than it read has a negative net: it cost the parent
  context instead of saving it.
- Not-worth-it spawns are the list to act on: the kinds of task to stop
  delegating, or to delegate with a tighter brief.
- Low uptake for a router suggestion is a finding about the trigger, not proof
  the skill is useless: check whether it fired on scratch paths (counted
  separately), and read `followed` beside it. The suggestion text carries the
  skill's main point, so an agent can act on it without loading the skill; a
  rule's `follow_through` patterns count that.
- Keep the sources apart. Each router row's `by` names the plugin whose
  router made the suggestion, so core's rules are judged on core's rows and a
  private overlay's on its own. `skills_self` counts the Skill calls no
  suggestion led to: the agent chose those from its skill list.
- Read each activity against its context carry. When carry dominates, the
  fix is a smaller context (a fresh session, a subagent for the long read, a
  lower budget), not a cheaper action. A process with a high cost per run
  and many runs is the one to script.
- Report counts. Never paste prompt text, descriptions or paths from the JSON
  into anything shared.
