#!/usr/bin/env python3
"""Is this agent setup earning its keep? Numbers from local transcripts only.

    python effectiveness.py                          # last 30 days, short table
    python effectiveness.py --since 7 --json out.json

READ-ONLY. It reads Claude Code transcripts under $CLAUDE_CONFIG_DIR/projects
(default ~/.claude/projects): <project>/<session>.jsonl, and the subagent files
<project>/<session>/subagents/agent-*.jsonl (and workflow steps under
subagents/workflows/) with their *.meta.json, plus
Gnomonkit's own state (~/.claude/skill-router/*.json, ~/.gnomonkit/interjector/,
~/.gnomonkit/freshness/) and the newest bench results.json. Besides --json it
writes its own cache (~/.gnomonkit/effectiveness/cache.json; --no-cache skips
it) and, from the CLI, the correction hand-labelling sample (see READINESS).
Nothing goes over the network. Standard library only.

COST. Transcripts can be hundreds of MB. Each file's result is cached by
(size, mtime), so a finished session is read once. Lines longer than
--line-cap are not parsed: an assistant line still counts as a turn (its id
and usage are read from its head and tail), and an over-long tool result
counts its line length as an ESTIMATED size.

EVERY FIGURE IS LABELLED. MEASURED means an exact usage field or a count.
ESTIMATED means a size in characters divided by 4.

SUBAGENTS. A subagent's meta.json names the parent's Agent tool_use id
(toolUseId); the parent's transcript holds that tool_use (in the session file,
or in another subagent's file for a nested spawn). Per spawn:
  spend        the subagent's own tokens, summed per assistant turn (one API
               message id) from its usage fields. MEASURED.
  kept_out     the tool results the subagent read, chars/4. That context never
               entered the parent. ESTIMATED.
  returned     what the parent received: the Agent tool result for a
               foreground spawn; the launch notice plus every distinct
               task-notification and hand-back for a background one. ESTIMATED.
  net_kept_out kept_out - returned.
  parent_turns_after  the parent's assistant turns after the result arrived,
               up to the next compaction (a compaction drops the context, so
               later turns would not have re-read it). MEASURED.
  reads_avoided net_kept_out x parent_turns_after. ESTIMATED.
  worth_it     reads_avoided > spend.
  small        the subagent made <= SMALL_TOOL_CALLS tool calls or read under
               SMALL_KEPT_TOKENS tokens: a lookup that could have run inline.
  notified_tokens  the subagent_tokens figure in a background task-notification,
               Claude Code's own number (its context at the finish, not a sum
               of turns), kept beside spend for comparison. MEASURED.
A subagent with no toolUseId in its meta is linked by its agent id instead
(the "agentId:" line of the launch result); one that still has no parent
(a workflow step, or a parent transcript that was deleted) is counted with
its spend but gets no verdict.
Spawns are grouped by type, project and week. Each group has spend (every
spawn) and linked_spend (the linked spawns alone), the figure to set beside
reads_avoided, which only linked spawns have. A week is the ISO week of the
spawn's start, in UTC, and carries days_in_window (how many days of it the
window covers) and partial (the window covers only part of it, as it does
the first and the last week), so a part week is not read as a whole one.

OUTPUT DIGEST (optional). If you run long commands through a wrapper that
prints a digest instead of the whole output, name it with --digest-command
(or the core plugin's digest_command option, or GNOMONKIT_DIGEST_COMMAND).
The wrapper is expected to start a digested result with a "[<name>] ...;
<N> chars" header and a "Full output: <path>" line. A shell call whose command
starts with `<name> `: printed is the tool result's size; full is the "Full
output" file's size when it still exists, else the char count in the header,
else (no header: the output was under the threshold and printed whole) the
printed size. With no name set, digests are not tracked.

MAIN CONTEXT. Main-session transcripts only (subagents/ left out), each API
message id once. Context per assistant turn = input + cache_read +
cache_creation from its usage fields. Per session: turns, peak, and re-read
(the sum of per-turn context). Overall: the share of turns and of re-read
tokens above each of CONTEXT_THRESHOLDS, sessions that compacted, all
MEASURED; and re-read if every turn were capped at the context budget (the
core plugin's context_budget option, GNOMONKIT_CONTEXT_BUDGET or
--context-budget, default 250k), ESTIMATED.

HOOKS, all MEASURED counts, read from hook additionalContext in the transcript:
router "skill: <plugin>:<name>" suggestions and their uptake (a Skill call for
it, or a Read of its SKILL.md, within UPTAKE_WINDOW tool calls; "already
loaded" when it was invoked earlier in that transcript, and left out of the
uptake share). Per skill: `by`, the suggestions per plugin prefix, so a core
suggestion stays apart from an overlay's or another library's; `followed`, the
suggestions after which a shell command in the window matched one of the
rule's `follow_through` patterns (from core's triggers.json, the files in
GNOMONKIT_TRIGGER_FILES, and every installed plugin's hooks/triggers.json),
so a suggestion acted on without loading the skill still shows. skills_self:
Skill calls no suggestion led to, by the name called; interlock blocks and how each
resolved within INTERLOCK_WINDOW calls (re-issued with `# interlock-ok:` after
or without a listing command, rewritten = a later unblocked command running
the same program, or abandoned); correction-watch fires; plugin-freshness
notices; palace precedent ids ([L<n>] / [E<n>]) handed to a session or a
subagent prompt, and the ones its assistant text cites afterwards. Core's own
lessons hook tags its items the same way, so its injections and citations are
counted here too, by hook event.

HISTORY (state.history_state), MEASURED from the logs core's history commands write,
never the words searched: `gnomonkit history search` runs in the window, how many
found something, how many were marked with `history mark` and how many marks
named a result that helped (the last mark of a search counts); lessons
injections and the distinct ids shown; the last index run.

COMPACTION, per project (the transcript folder name), main sessions only:
  base         the median over sessions of the minimum context in a session's
               first BASE_TURNS turns (system prompt, CLAUDE.md, skills, memory).
  growth       per user prompt, the peak context before the next prompt or
               compaction minus the context just before the prompt; median and
               p90. MEASURED.
  events       every compaction: context at the event (compactMetadata
               preTokens, else the last turn before it), auto or manual when
               the transcript says, and two harm signals. Re-discovery: the
               share of the next REREAD_WINDOW Read calls that re-read a file
               read before the compaction (post), against the share of the
               REREAD_WINDOW reads before it that re-read a file read earlier
               still (pre; needs PRE_MIN_READS reads). Correction: a prompt
               within the next CORRECTION_PROMPTS that core's correction_watch
               reads as a correction (the hook's own is_correction).

BUDGETS (--recommend prints this section as JSON). Per project: start at
base + p90 growth, rounded up to BUDGET_STEP. Raise one step when at least
HARM_MIN_COMPACTIONS compactions show harm (post re-read above the project's
own pre rate by HARM_MARGIN, or a correction after CORRECTION_HARM_SHARE of
them). Lower one step when OVER_BUDGET_SHARE of sessions peak above the
current budget and compactions show no harm, or when under
RARE_COMPACTION_SHARE of sessions compact and p90 growth is under
SMALL_GROWTH. Clamped to [BUDGET_MIN, BUDGET_MAX]. Confidence by sample size
(context windows, i.e. sessions plus compactions, and prompts);
a low-confidence project keeps its current budget. The current budget is read
from env.GNOMONKIT_CONTEXT_BUDGET in the project's .claude/settings.local.json
or settings.json (at the cwd its transcripts record), else the global budget.
Nothing is written to any project: applying a recommendation is left to the
caller.

READINESS. One row per metric: value, n, a target from READINESS_TARGETS and a
status (ok / warn / fail; no-data below the row's min_n). Skill uptake: the
router's suggestions of a core skill not already loaded, and the share taken up
(a Skill call for that skill, or a Read of its SKILL.md, within UPTAKE_WINDOW
tool calls), as the HOOKS router uptake counts it; session counts stay in its detail. Interlock
false blocks (proxy): blocks
followed within INTERLOCK_WINDOW calls by an equivalent command (same program
and targets, every flag kept) running unblocked without the override marker,
or by a next user prompt that lets it through.
Correction precision: labelled yes / labelled in the sample file once it has
min_n labels, else the share of fires a stricter phrase list also matches.
Hook latency / errors: core hook runs recorded as transcript attachments
(hook_success, hook_non_blocking_error, hook_cancelled) and Stop hook summaries;
quiet runs are not recorded. Bench: the newest <bench root>/*/results.json,
applied k/n per scenario and arm. Leak-gate catches: shell calls that ran a
gate, and those whose output reports a finding.

USAGE. Each assistant API message once (across the session and its
subagents): tokens by UTC day (daily_timezone says so beside the daily rows),
project and session, and est_cost_usd from
MODEL_PRICES, labelled an API-equivalent estimate; an unlisted model has no
cost and is counted as unpriced.

SUBSCRIPTION. A schedule file (the core plugin's subscription_file option,
GNOMONKIT_SUBSCRIPTION_FILE, else ~/.gnomonkit/subscription.json) lists
{"from": "YYYY-MM", "plan", "monthly_usd"}; the latest from at or before a month
is in effect. Plan dollars per day = monthly_usd / days in that month. Per
month: plan dollars for the window's days in it, the API-equivalent cost, and
their multiple. A project's or session's share = its part of each month's
API-equivalent cost x that month's plan dollars in the window. Month to date:
spend so far, a linear projection for the month, and days of plan value used.
No file: those fields are null and the output says how to write one.

ACTIVITIES (--activities prints them). Where the API-equivalent cost goes. Each
assistant turn is taken exactly as USAGE takes it and priced from MODEL_PRICES
in two parts: context carry (cache read + cache write: re-sending and storing
the context) and work (input + output). A cheap action taken in a large
context is expensive, and the carry part is what shows it.
  attribution  the turn's cost goes to the activity of the tool calls that turn
               made (ACTIVITY_RULES, ordered, first match wins), split evenly
               across its calls; a turn with no tool call is reasoning/reply.
               A turn that splits counts one turn for each activity it touches.
  subagents    every turn of a subagent transcript goes to "subagent: <type>",
               and the subagent's own activities are its breakdown (inner).
  per activity cost, share of the total, context carry and work, turns,
               sessions, and the share of plan dollars by the SUBSCRIPTION
               allocation; "shell other" lists its top programs.
  processes    repeated command sequences as automate-repeats finds them (same
               step shapes, PROCESS_N steps, seen in PROCESS_MIN_SESSIONS
               sessions), each with the summed attributed cost of the calls in
               every run and the cost per run: the "is it worth scripting" number.
  bands        the share of cost (and of turns) in turns whose context (input +
               cache read + cache write) is above each of ACTIVITY_BANDS, all
               turns, subagents included.
Costs are ESTIMATED (API-equivalent); turns and runs are MEASURED.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import importlib.util
import re
import statistics
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

VERSION = "0.30.1"
SCAN_VERSION = 12              # bump when scan_file's output changes; old cache entries are ignored
LINE_CAP = 2_000_000
CHARS_PER_TOKEN = 4
UPTAKE_WINDOW = 10               # tool calls after a router suggestion that count as uptake
FOLLOW_KEY_MAX = 200             # chars of each shell command kept for the follow-through check
INTERLOCK_WINDOW = 10            # tool calls after a block that decide how it resolved
SMALL_TOOL_CALLS = 3
SMALL_KEPT_TOKENS = 2000
CONTEXT_THRESHOLDS = (200_000, 300_000, 500_000, 800_000)
DEFAULT_CONTEXT_BUDGET = 250_000
SETTING_BUDGET_MAX = 10_000_000  # a configured budget above this is clamped to it
SETTINGS_MAX_BYTES = 1_000_000  # a project settings file larger than this is not read for a budget
TOP_SESSIONS = 5                 # sessions the printed table lists by re-read
DESC_MAX = 80
BASE_TURNS = 3                   # a session's first turns, whose minimum context is the base
REREAD_WINDOW = 40               # Read calls compared before and after a compaction
PRE_MIN_READS = 10               # reads needed before a compaction to measure the pre rate
CORRECTION_PROMPTS = 5           # prompts after a compaction checked for a correction
BUDGET_STEP = 50_000
BUDGET_MIN = 150_000
BUDGET_MAX = 500_000
HARM_MARGIN = 0.10               # post re-read share above the pre share that counts as harm
HARM_MIN_COMPACTIONS = 3
CORRECTION_HARM_SHARE = 0.34     # share of compactions followed by a correction that counts as harm
OVER_BUDGET_SHARE = 0.30         # share of sessions peaking above budget that is "often"
RARE_COMPACTION_SHARE = 0.10
SMALL_GROWTH = 50_000
CONFIDENCE_MEDIUM = (5, 20)      # (context windows = sessions + compactions, prompts)
CONFIDENCE_HIGH = (20, 100)
MIN_SESSION_TURNS = 5            # sessions shorter than this say nothing about a project's base
SHELLS = ("Bash", "PowerShell")
REDO_CALLS = 12                  # parent tool calls after a subagent returned that can count as redoing its work
REDO_KEY_MAX = 160
CALL_KEYS_MAX = 2000             # call keys kept per transcript for the redo signal
REJECT_TURNS = 3                 # parent turns after a return checked for text calling the result wrong
CORRECTED_PROMPTS = 2            # prompts after a return checked for a correction
THIN_N = 5                       # a rate over fewer measured spawns is marked thin
CHEAP_TYPES = ("collector", "checker")
# Assistant text that names the agent (or its result) and calls it wrong. Narrow on purpose: it under-reports.
_REJECT = re.compile(
    r"\b(sub-?agent|agent|its (?:result|answer|report|output)|that (?:result|answer|report))\b"
    r"[^.\n]{0,60}\b(was wrong|is wrong|were wrong|got (?:it|that|this) wrong|incorrect|incomplete|"
    r"made (?:it|that|this) up|made up|hallucinat\w*|missed (?:it|them|the|some)|not (?:accurate|right))\b",
    re.I)
AGENT_TOOLS = ("Agent", "Task")

# Tagged blocks in transcript text are found with str.find (_blocks, _tag) rather than a lazy regex:
# a regex retries from every unclosed opening tag and goes quadratic on text with many of them.
NOTIF_OPEN, NOTIF_CLOSE = "<task-notification>", "</task-notification>"
_HANDBACK = re.compile(r'<agent-message from="([A-Za-z0-9_-]+)">(.*?)(?:</agent-message>|\Z)', re.S)
_TAGS = ("task-id", "tool-use-id", "subagent_tokens")
_AGENT_ID = re.compile(r"agentId: ([A-Za-z0-9_-]+)")
# [ \t]*: \s* re-scanned blank runs. A router whose skills belong to other plugins prints them bare and
# names itself after the name, "skill: <name>  (router: <plugin>)"; the mark wins over the name's prefix.
_SKILL_LINE = re.compile(r"(?m)^[ \t]*skill:\s*([A-Za-z0-9_.:-]+)(?:[ \t]+\(router:\s*([A-Za-z0-9_-]+)\))?")
_INJECTED = re.compile(r"\[([LE])(\d{1,7})\b")
_CITED = re.compile(r"\b([LE])(\d{1,7})\b")
_DIGEST_PATH = re.compile(r"Full output: (\S+)")
_LISTING = re.compile(
    r"(?i)^\s*(ls|dir|tree|find|du|stat|cat|head|wc|get-childitem|gci|test-path|get-item|lsblk|blkid|df|"
    r"git\s+(status|branch|log|worktree\s+list|ls-files|ls-remote|show|stash\s+list|diff)|"
    r"(zfs|zpool)\s+(list|status|get)|kubectl\s+(get|describe)|docker\s+(ps|volume\s+ls|images)|"
    r"gh\s+\S+\s+(list|view)|aws\s+s3\s+ls|psql\b.*\\d|sqlite3\b.*\.tables)")
INTERLOCK_TEXT = "IRREVERSIBLE ACTION BLOCKED"
CORRECTION_TEXT = "reads like a correction"
FRESHNESS_TEXT = "Plugin update pending"
HOOK_RUN_TYPES = {"hook_success": None, "hook_blocking_error": None, "hook_non_blocking_error": "error",
                  "hook_cancelled": "cancelled"}   # a blocking error is a hook saying no on purpose, not a failure
FIRED_MAX = 50                   # correction-watch fires kept per transcript for the hand-labelling sample
SAMPLE_CHARS = 300               # characters of a prompt kept in that sample
SAMPLE_MAX = 200                 # rows in the sample file (labelled rows are always kept)
# A stricter correction matcher than correction_watch's: only phrasings that are almost never anything
# else. The share of the hook's fires it agrees with is the precision proxy until a sample is labelled.
_STRICT_CORRECTION = re.compile(
    r"(?i)\b(you (didn'?t|did not|forgot|missed|ignored|never) |i (already |just )?told you\b|"
    r"not what i (asked|meant|wanted)|i already (told|said|asked)|i had to tell you|"
    r"(that'?s|this is) (wrong|incorrect|not true)\b|why did you\b|did you not\b|"
    r"(yet|once) again\b|(like|as) i (said|told you)\b)")
# The user's next prompt after an interlock block reads as letting the command through.
_OVERRIDE = re.compile(
    r"(?i)\b(go ahead|go for it|(it'?s|that'?s|this is|it was|that was) (fine|ok|okay|safe|harmless)|"
    r"just (run|do) it|run it( anyway)?|do it anyway|override|false (positive|alarm)|not destructive|"
    r"interlock-ok)\b")
# A gate ran when one command segment (split on | ; & and newlines) calls python and then names the
# gate's script. Two linear searches per segment; one regex spanning both went quadratic on long input.
_PY_CALL = re.compile(r"(?i)\bpy(?:thon[0-9.]*)?(?:\.exe)?\"?\s")
_GATE_SCRIPT = {"leakcheck": re.compile(r"(?i)\bleakcheck\.py\b"),
                "provenance": re.compile(r"(?i)\bprovenance_check\.py\b")}
_GITLEAKS = re.compile(r"(?i)\bgitleaks\s+(git|detect|dir|protect)\b")
_CMD_SEP = re.compile(r"[|;&\n]")
_GATE_CATCH = {"leakcheck": re.compile(r"leakcheck: \d+ finding"),
               "provenance": re.compile(r"provenance: \d+ line\(s\) read as evidence"),
               "gitleaks": re.compile(r"leaks found: [1-9]")}
_GATE_SEEN = re.compile(r"(?m)^(leakcheck|provenance|leak gates):")
# A script name starts where a run of name characters starts: tried from inside the run as well, a
# long dotted run with no script suffix took quadratic time.
_SCRIPT = re.compile(r"(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]+\.(?:py|sh|ps1|js|cmd|bat))\b")


def _blocks(text: str, open_tag: str, close_tag: str):
    """(whole, body) for each open_tag ... close_tag block, left to right, found with str.find."""
    i = text.find(open_tag)
    while i >= 0:
        j = text.find(close_tag, i + len(open_tag))
        if j < 0:
            return                    # no close after this opening tag means none after any later one
        yield text[i:j + len(close_tag)], text[i + len(open_tag):j]
        i = text.find(open_tag, j + len(close_tag))


def _tag(body: str, name: str) -> str:
    """The stripped text of the first <name>...</name> in body, else ""."""
    for _, inner in _blocks(body, "<%s>" % name, "</%s>" % name):
        return inner.strip()
    return ""


def gates_ran(cmd: str) -> set:
    """The leak gates a shell command ran: a python call followed by the gate's script in one segment."""
    ran = {"gitleaks"} if _GITLEAKS.search(cmd) else set()
    for seg in _CMD_SEP.split(cmd):
        py = _PY_CALL.search(seg)
        if py:
            ran.update(g for g, rx in _GATE_SCRIPT.items() if rx.search(seg, py.end()))
    return ran


def _load_is_correction():
    """core's correction_watch.is_correction: the same phrase families the hook fires on. None if missing."""
    p = Path(__file__).resolve().parents[2] / "hooks" / "correction_watch.py"
    try:
        spec = importlib.util.spec_from_file_location("gnomonkit_correction_watch", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.is_correction
    except Exception:
        return None


is_correction = _load_is_correction()


def _load_detect_repeats():
    """automate-repeats' detect_repeats: its step shapes and sequence finder, so a process here is the same
    process that skill reports. None if it is missing (processes are then left out)."""
    p = Path(__file__).resolve().parents[1] / "automate-repeats" / "detect_repeats.py"
    try:
        spec = importlib.util.spec_from_file_location("gnomonkit_detect_repeats", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


DR = _load_detect_repeats()

# ---------------------------------------------------------------------------
# activity taxonomy
# ---------------------------------------------------------------------------
# Every tool call gets one activity. The rules are tried in this order and the first match wins, so a
# shell call that runs the tests and then commits is "shipping". A rule matches on:
#   tools   exact tool names;
#   server  a regex on the MCP server name (MCP tools are "mcp__<server>__<tool>");
#   shell   a regex searched in each step of a shell command (split as automate-repeats splits it, on
#           && || ; | and newlines, with heredoc bodies and quoted text removed), lower-cased;
#   script  a regex searched in the whole shell command (patch scripts whose writes sit in a heredoc).
# Two kinds of activity are built from the call rather than listed: "mcp: <server>" for an MCP tool no
# rule took, and "shell other" (the program kept as its detail) for a shell call no rule took. A tool
# no rule names is "other: <tool name>". A turn with no tool call is REASONING. To extend the
# taxonomy, add a row; keep the more specific rows first.
SHIPPING = "shipping"
REASONING = "reasoning/reply"
SHELL_OTHER = "shell other"
ACTIVITY_RULES = (
    # commits, pushes, PRs opened or merged, releases
    {"activity": SHIPPING,
     "shell": r"\bgit\s+(?:-c\s+\S+\s+)*(?:commit|push|tag|merge)\b|"
              r"\bgh\s+(?:pr\s+(?:create|merge|ready)|release\s+create)\b"},
    # running tests, builds and linters
    {"activity": "tests and builds",
     "shell": r"\b(?:pytest|py\.test|tox|nox|unittest|vitest|jest|mypy|ruff|eslint|flake8|tsc|make|cmake|ninja|"
              r"gradlew?|mvn|msbuild)\b|\bflutter\s+(?:test|build|analyze)\b|\b(?:npm|pnpm|yarn|bun)\s+"
              r"(?:test|t|build|run\s+[a-z:_-]*(?:test|build|lint|check)[a-z:_-]*)\b|"
              r"\bcargo\s+(?:test|build|check|clippy|nextest)\b|\bdotnet\s+(?:build|test|publish)\b|"
              r"\bgo\s+(?:test|build|vet)\b"},
    # waiting on CI or on background work: check and run watchers, sleep loops, task-output polling
    {"activity": "CI and waiting", "tools": {"Monitor", "TaskOutput", "BashOutput", "Sleep"},
     "shell": r"\bgh\s+(?:pr\s+checks|run\s+(?:watch|view|list))\b|^(?:sleep|start-sleep|wait)\b"},
    # changing files: the edit tools, in-place sed or perl, patches, and scripts that write files
    {"activity": "editing", "tools": {"Edit", "Write", "MultiEdit", "NotebookEdit"},
     "shell": r"\bsed\s+(?:-[a-z]*i|--in-place)|\bperl\s+-[a-z]*i|^patch\b|\bgit\s+apply\b|^set-content\b|"
              r"^add-content\b|\bout-file\b",
     "script": r"\.write_text\(|\.write_bytes\(|open\([^()]*[\"'][wa]b?\+?[\"']"},
    # reading code: file reads and searches, and git's look-around commands
    {"activity": "reading code", "tools": {"Read", "Grep", "Glob", "LS", "LSP", "NotebookRead"},
     "shell": r"^(?:cat|head|tail|less|more|rg|grep|egrep|findstr|awk|wc|ls|dir|tree|find|type|get-content|"
              r"select-string|get-childitem)\b|\bsed\s+-n\b|\bgit\s+(?:status|diff|log|show|blame|grep|ls-files)\b"},
    # memory and search: the palace tools, core's local history and lessons, and the web
    {"activity": "search and memory", "tools": {"WebSearch", "WebFetch"}, "server": r"palace",
     "shell": r"^mempalace\b|\bgnomonkit(?:\.py)?\s+(?:history|lessons)\b"},
    # driving other agents and tasks
    {"activity": "orchestration",
     "tools": {"Agent", "Task", "SendMessage", "TaskStop", "ListAgents", "TaskCreate", "TaskUpdate", "TaskList",
               "TaskGet", "TodoWrite", "EnterWorktree", "ExitWorktree"}},
    # what the person sees or answers: questions, artifacts and pages, document tools
    {"activity": "user-facing",
     "tools": {"AskUserQuestion", "Artifact", "ArtifactComments", "ArtifactData", "ExitPlanMode"},
     "server": r"(?i)docs?$"},
)
_RULES = [dict(r, shell=re.compile(r["shell"]) if r.get("shell") else None,
               script=re.compile(r["script"]) if r.get("script") else None,
               server=re.compile(r["server"]) if r.get("server") else None) for r in ACTIVITY_RULES]
# A quoted string opens only at a quote with no backslash before it, so a run of \" cannot make every
# one of them a start that scans to the end of the text.
_QUOTED_TEXT = re.compile(r"""((?<!\\)"(?:[^"\\]|\\.)*"|'[^']*')""")
_NOT_A_PROGRAM = {"cd", "pushd", "popd", "set-location", "export", "set", "for", "while", "if", "until", "echo"}


def _shell_segments(cmd: str) -> list:
    segs = DR.split_steps(cmd) if DR else [x.strip() for x in re.split(r"&&|\|\||;|\||\n", cmd)]
    out = []
    for s in segs:
        s = _QUOTED_TEXT.sub(" ", s).strip().lower()
        s = re.sub(r"^(?:[a-z_][a-z0-9_]*=\S*\s+)+", "", s)              # FOO=1 cmd
        s = re.sub(r"^(?:sudo|time|nohup|exec|do|then|else|&|!|\{|\()\s+", "", s)
        first, _, rest = s.partition(" ")
        first = re.split(r"[\\/]", first)[-1] or first                    # /usr/bin/git -> git
        first = re.sub(r"\.(?:exe|cmd|bat)$", "", first)
        s = (first + " " + rest).strip()
        if s:
            out.append(s)
    return out


def _program(cmd: str, segments: list) -> str:
    """The first program a shell command runs, past cd, echo and variable assignments: the detail of
    "shell other". automate-repeats' step shape names it when available (it reads a quoted interpreter
    path, PowerShell's & and wrappers such as timeout)."""
    if DR:
        progs = [sh.split()[0] for sh in (DR.step_shape(s, True) for s in DR.split_steps(cmd)) if sh]
    else:
        progs = [s.split()[0] for s in segments if s.split()]
    progs = [p[:40] for p in progs if re.fullmatch(r"[a-z0-9][a-z0-9_.+-]*", p)]
    return next((p for p in progs if p not in _NOT_A_PROGRAM), progs[0] if progs else "unknown")


def classify_call(name: str, inp: dict | None) -> tuple:
    """(activity, detail) for one tool call, by ACTIVITY_RULES. detail is the program for "shell other"."""
    name = name or ""
    inp = inp if isinstance(inp, dict) else {}
    server = name.split("__")[1] if name.startswith("mcp__") and name.count("__") >= 2 else ""
    shell = name in SHELLS
    cmd = str(inp.get("command") or "") if shell else ""
    segs = _shell_segments(cmd) if shell else []
    for r in _RULES:
        if name in r.get("tools", ()):
            return r["activity"], ""
        if server and r["server"] and r["server"].search(server):
            return r["activity"], ""
        if shell and ((r["shell"] and any(r["shell"].search(s) for s in segs))
                      or (r["script"] and r["script"].search(cmd))):
            return r["activity"], ""
    if server:
        return "mcp: " + server, ""
    if shell:
        return SHELL_OTHER, _program(cmd, segs)
    return "other: " + (name or "unknown"), ""


def est(chars: float) -> int:
    return int(round(chars / CHARS_PER_TOKEN))


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def gnomonkit_home() -> Path:
    return Path(os.environ.get("GNOMONKIT_HOME") or Path.home() / ".gnomonkit")


def digest_command() -> str:
    """The output-digest wrapper to measure, from settings. Empty: none."""
    return (os.environ.get("CLAUDE_PLUGIN_OPTION_DIGEST_COMMAND") or os.environ.get("GNOMONKIT_DIGEST_COMMAND")
            or "").strip()


def _profile(key: str):
    """The personal profile's value for `key` (checked against its schema), or None. Never raises."""
    try:
        core = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if core not in sys.path:
            sys.path.append(core)
        from gnomonkit_cmds import _common
        return _common.checked_profile_value(key)
    except Exception:  # noqa: BLE001 - the profile is optional
        return None


def budget_value(raw) -> int | None:
    """A context budget as tokens, or None when raw is not one. Infinity, NaN, zero and negatives are not
    budgets; anything above SETTING_BUDGET_MAX is clamped to it, so "1e999" cannot overflow int()."""
    if raw is None or isinstance(raw, bool):
        return None
    text = str(raw).strip().replace(",", "").replace("_", "")
    if not text or len(text) > 64:
        return None
    try:
        f = float(text)
    except ValueError:
        return None
    if not math.isfinite(f) or f < 1:
        return None
    return int(min(f, SETTING_BUDGET_MAX))


def context_budget() -> int:
    """The main-session context budget: the core plugin's context_budget option, else the personal
    profile's context.context_budget. Default 250k (also when the one that is set is not a budget)."""
    raw = os.environ.get("CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET") or os.environ.get("GNOMONKIT_CONTEXT_BUDGET")
    if not (raw or "").strip():
        return budget_value(_profile("context.context_budget")) or DEFAULT_CONTEXT_BUDGET
    return budget_value(raw) or DEFAULT_CONTEXT_BUDGET


def _digest_full(name: str):
    return re.compile(r"\[%s\][^\n]*?;\s*([\d,]+) chars" % re.escape(name))


def _flat(content) -> str:
    """Text of a message or tool_result content (str, or a list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, str):
                out.append(b)
            elif isinstance(b, dict):
                if isinstance(b.get("text"), str):
                    out.append(b["text"])
                elif "content" in b:
                    out.append(_flat(b["content"]))
        return "\n".join(out)
    return ""


def _ts(d: dict) -> str:
    return str(d.get("timestamp") or "")


# ---------------------------------------------------------------------------
# one transcript -> a compact, cacheable summary
# ---------------------------------------------------------------------------

def scan_file(path: Path, line_cap: int = LINE_CAP, digest: str = "") -> dict:
    """`digest` is the output-digest wrapper's command name; empty means none is tracked."""
    digest_full = _digest_full(digest) if digest else None
    turns = 0
    seen: set = set()
    usage: dict = {}
    boundaries: list = []
    calls: list = []                 # [tool_use_id, name, key]
    call_ts: list = []               # each call's timestamp, by index into calls
    agent_calls: dict = {}
    agent_results: dict = {}
    notes: dict = {}                 # hash -> note
    digest_ids: dict = {}
    digests: list = []
    blocks: list = []
    hook_ctx: list = []
    cites: list = []                 # [ord, [ids]]
    prompt_ids: list = []
    first_user = True
    kept = 0
    long_lines = 0
    long_est = 0
    first_ts = last_ts = ""
    cwd = ""
    reads: list = []                 # normcased Read file paths, in order
    prompts: list = []               # per typed prompt: [usage position, is a correction]
    compacts: list = []              # per compaction: positions in reads / usage / prompts, and its metadata
    turn_meta: dict = {}             # message id -> [timestamp, model, 1h cache write tokens, speed]
    title = ai_title = ""
    prompt_calls: list = []          # per typed prompt: [tool calls before it, reads as an interlock override]
    fired: list = []                 # prompts the correction matcher fires on (sample for hand labelling)
    hook_runs: list = []             # per recorded hook run: [ts, event, script, ms, error kind or None]
    hook_seen: set = set()
    shell_cmd: dict = {}             # shell tool_use id -> command
    gates: list = []                 # per shell call that ran or tripped a leak gate
    turn_acts: dict = {}             # message id -> [[activity, detail]] per tool call it made
    call_mid: list = []              # per tool call, in order: its message id
    proc_steps: list = []            # [step shape, index in call_mid], as automate-repeats shapes them
    act_seen: set = set()
    prompt_flags: list = []          # per typed prompt: True / False / None (no matcher)
    call_keys: list = []             # [tool, key, call index] per Read / Grep / Glob / shell call
    reject_turns: list = []          # turn numbers whose assistant text calls an agent's result wrong

    def add_prompt(text, ts=""):
        t = (text or "").strip()
        if t and not t.startswith("<"):
            c = bool(is_correction(t)) if is_correction else None
            prompts.append([len(usage), c])
            prompt_flags.append(c)
            prompt_calls.append([len(calls), bool(_OVERRIDE.search(t))])
            if c and len(fired) < FIRED_MAX:
                fired.append({"ts": ts, "text": t[:SAMPLE_CHARS], "strict": bool(_STRICT_CORRECTION.search(t))})

    def add_hook_run(ts, event, tuid, command, ms, err):
        m = _SCRIPT.findall(command or "")
        script = m[-1] if m else (str(command or "").split() or [""])[0][:60]
        key = (tuid, event, script)
        if key in hook_seen:
            return
        hook_seen.add(key)
        hook_runs.append([ts, event, script, ms if isinstance(ms, (int, float)) else None, err])

    def add_turn(mid, u, model="", ts=""):
        nonlocal turns
        if mid not in seen:
            seen.add(mid)
            turns += 1
        meta = turn_meta.setdefault(mid, [ts, model or "", 0, ""])
        meta[0] = meta[0] or ts
        meta[1] = meta[1] or model or ""
        if u:
            cc = u.get("cache_creation")
            if isinstance(cc, dict):
                try:
                    meta[2] = max(meta[2], int(cc.get("ephemeral_1h_input_tokens") or 0))
                except (TypeError, ValueError):
                    pass
            if isinstance(u.get("speed"), str):
                meta[3] = u["speed"]
            cur = usage.setdefault(mid, [0, 0, 0, 0])
            for i, k in enumerate(("input_tokens", "cache_creation_input_tokens",
                                   "cache_read_input_tokens", "output_tokens")):
                try:
                    cur[i] = max(cur[i], int(u.get(k) or 0))
                except (TypeError, ValueError):
                    pass

    def find_notes(text, ts):
        for whole, body in _blocks(text, NOTIF_OPEN, NOTIF_CLOSE):
            h = hashlib.sha1(whole.encode("utf-8", "replace")).hexdigest()[:16]
            if h in notes:
                continue
            g = {k: _tag(body, k) for k in _TAGS}
            tok = g["subagent_tokens"].replace(",", "")
            notes[h] = {"aid": g["task-id"], "tid": g["tool-use-id"], "chars": len(whole), "ord": turns,
                        "ts": ts, "tokens": int(tok) if tok.isdigit() else None, "result": "<result>" in body,
                        "ci": len(calls), "pi": len(prompts)}
        for m in _HANDBACK.finditer(text):
            h = hashlib.sha1(m.group(0).encode("utf-8", "replace")).hexdigest()[:16]
            if h not in notes:
                notes[h] = {"aid": m.group(1), "tid": "", "chars": len(m.group(0)), "ord": turns, "ts": ts,
                            "tokens": None, "result": True, "ci": len(calls), "pi": len(prompts)}

    with open(path, "rb") as fh:
        for raw in fh:
            if len(raw) > line_cap:
                long_lines += 1
                if b'"type":"assistant"' in raw:
                    m = re.search(rb'"id":"(msg_[A-Za-z0-9_-]+)"', raw[:16384])
                    u = {}
                    for k in (b"input_tokens", b"cache_creation_input_tokens", b"cache_read_input_tokens",
                              b"output_tokens"):
                        mm = re.search(rb'"' + k + rb'":(\d+)', raw[-16384:])
                        if mm:
                            u[k.decode()] = int(mm.group(1))
                    mm = re.search(rb'"model":"([^"]{1,80})"', raw[:16384])
                    mt = re.search(rb'"timestamp":"([^"]{1,40})"', raw[-16384:]) or \
                        re.search(rb'"timestamp":"([^"]{1,40})"', raw[:16384])
                    add_turn(m.group(1).decode() if m else "long%d" % long_lines, u,
                             mm.group(1).decode() if mm else "", mt.group(1).decode() if mt else "")
                elif b'"type":"user"' in raw and b'"tool_result"' in raw:
                    kept += len(raw)
                    long_est += 1
                    m = re.search(rb'"tool_use_id":"([A-Za-z0-9_-]+)"', raw[:16384])
                    if m and m.group(1).decode() in agent_calls:
                        agent_results[m.group(1).decode()] = {"chars": len(raw), "ord": turns, "async": False,
                                                              "aid": "", "estimated_line": True,
                                                              "ci": len(calls), "pi": len(prompts)}
                continue
            if raw.startswith(b'{"type":"api-request') or not (
                    b'"type":"assistant"' in raw or b'"type":"user"' in raw or b"hook_additional_context" in raw
                    or b"queued_command" in raw or b"compact_boundary" in raw or b"hook_" in raw
                    or b'-title"' in raw):
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            t = d.get("type")
            ts = _ts(d)
            if ts:
                first_ts = first_ts or ts
                last_ts = max(last_ts, ts)
            if not cwd and isinstance(d.get("cwd"), str):
                cwd = d["cwd"]
            if t == "custom-title" and isinstance(d.get("customTitle"), str):
                title = d["customTitle"][:DESC_MAX]
            elif t == "ai-title" and isinstance(d.get("aiTitle"), str):
                ai_title = d["aiTitle"][:DESC_MAX]
            if t == "assistant":
                msg = d.get("message") or {}
                mid = msg.get("id") or d.get("requestId") or d.get("uuid")
                add_turn(mid, msg.get("usage") or {}, str(msg.get("model") or ""), ts)
                for b in msg.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        name = b.get("name") or ""
                        tid = b.get("id") or ""
                        if (mid, tid or len(call_mid)) not in act_seen:
                            act_seen.add((mid, tid or len(call_mid)))
                            turn_acts.setdefault(mid, []).append(list(classify_call(name, inp)))
                            if DR:
                                proc_steps.extend([sh, len(call_mid)] for sh in DR.call_steps(name, inp))
                            call_mid.append(mid)
                        if name == "Skill":
                            key = str(inp.get("skill") or "")
                        elif name in SHELLS:
                            key = str(inp.get("command") or "")[:600]
                        else:
                            key = str(inp.get("file_path") or inp.get("path") or inp.get("pattern") or "")[:300]
                        calls.append([tid, name, key])
                        call_ts.append(ts)
                        if len(call_keys) < CALL_KEYS_MAX and key and \
                                (name in SHELLS or name in ("Read", "Grep", "Glob")):
                            call_keys.append([name, key[:REDO_KEY_MAX] if name != "Read"
                                              else os.path.normcase(key)[:REDO_KEY_MAX], len(calls) - 1])
                        if name in SHELLS:
                            shell_cmd[tid] = key
                        if name == "Read" and key:
                            reads.append(os.path.normcase(key))
                        if name in AGENT_TOOLS:
                            agent_calls[tid] = {"ord": turns, "ts": ts, "type": str(inp.get("subagent_type") or ""),
                                                "model": str(inp.get("model") or "")[:60],
                                                "bg": bool(inp.get("run_in_background")),
                                                "desc": str(inp.get("description") or "")[:DESC_MAX]}
                        elif digest and name in SHELLS and key.lstrip().startswith(digest + " "):
                            digest_ids[tid] = ts
                    elif b.get("type") == "text":
                        if turns not in reject_turns and _REJECT.search(b.get("text") or ""):
                            reject_turns.append(turns)
                        ids = sorted({m.group(1) + m.group(2) for m in _CITED.finditer(b.get("text") or "")})
                        if ids:
                            cites.append([turns, ids])
            elif t == "user":
                msg = d.get("message") or {}
                c = msg.get("content")
                typed = not d.get("isMeta") and not d.get("isCompactSummary")
                if isinstance(c, str):
                    if typed:
                        add_prompt(c, ts)
                    if first_user:
                        prompt_ids = sorted({m.group(1) + m.group(2) for m in _INJECTED.finditer(c)})
                    find_notes(c, ts)
                elif isinstance(c, list):
                    tur = d.get("toolUseResult")
                    if typed and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
                        add_prompt(" ".join(b.get("text") or "" for b in c
                                            if isinstance(b, dict) and b.get("type") == "text"), ts)
                    for b in c:
                        if not isinstance(b, dict):
                            continue
                        if b.get("type") == "tool_result":
                            text = _flat(b.get("content"))
                            n = len(text)
                            kept += n
                            tid = b.get("tool_use_id") or ""
                            if tid in agent_calls:
                                is_async = (isinstance(tur, dict) and bool(tur.get("isAsync"))) or \
                                    "Async agent launched" in text[:200]
                                m = _AGENT_ID.search(text)
                                aid = (tur.get("agentId") if isinstance(tur, dict) else None) or \
                                    (m.group(1) if m else "")
                                agent_results[tid] = {"chars": n, "ord": turns, "async": is_async, "aid": aid,
                                                      "ci": len(calls), "pi": len(prompts)}
                            if tid in digest_ids and tid not in {x["tid"] for x in digests}:
                                digests.append({"tid": tid, "ts": digest_ids[tid], "printed": n, "ord": turns,
                                                "text_head": text[:600]})
                            if tid in shell_cmd:
                                cmd = shell_cmd.pop(tid)
                                ran = gates_ran(cmd)
                                caught = {g for g, rx in _GATE_CATCH.items() if rx.search(text)}
                                if not ran and "git commit" in cmd and _GATE_SEEN.search(text):
                                    ran = {"commit_hook"}
                                if ran or caught:
                                    gates.append({"ts": ts, "ran": sorted(ran), "caught": sorted(caught)})
                            if b.get("is_error") and INTERLOCK_TEXT in text and \
                                    tid not in {x["tid"] for x in blocks}:
                                blocks.append({"tid": tid, "ts": ts})
                        elif b.get("type") == "text":
                            if first_user:
                                prompt_ids = sorted({m.group(1) + m.group(2)
                                                     for m in _INJECTED.finditer(b.get("text") or "")})
                            find_notes(b.get("text") or "", ts)
                first_user = False
            elif t == "attachment":
                a = d.get("attachment") or {}
                at = a.get("type")
                if at == "hook_additional_context":
                    hook_ctx.append({"idx": len(calls), "ord": turns, "ts": ts, "event": str(a.get("hookEvent") or ""),
                                     "tuid": str(a.get("toolUseID") or ""), "text": _flat(a.get("content"))})
                elif at in HOOK_RUN_TYPES:
                    err = "timed_out" if a.get("timedOut") else HOOK_RUN_TYPES[at]
                    add_hook_run(ts, str(a.get("hookEvent") or ""), str(a.get("toolUseID") or ""),
                                 a.get("command"), a.get("durationMs"), err)
                elif at == "queued_command":
                    p = a.get("prompt")
                    find_notes(p if isinstance(p, str) else _flat(p), ts)
            elif t == "system" and d.get("subtype") == "stop_hook_summary":
                errs = [str(e) for e in d.get("hookErrors") or []]
                for info in d.get("hookInfos") or []:
                    if isinstance(info, dict):
                        cmd = str(info.get("command") or "")
                        m = _SCRIPT.findall(cmd)
                        bad = bool(m) and any(m[-1] in e for e in errs)
                        add_hook_run(ts, "Stop", str(d.get("toolUseID") or ""), cmd, info.get("durationMs"),
                                     "error" if bad else None)
            elif t == "system" and d.get("subtype") == "compact_boundary":
                boundaries.append(turns)
                cm = d.get("compactMetadata") if isinstance(d.get("compactMetadata"), dict) else {}
                trig = cm.get("trigger")
                pre_tok = cm.get("preTokens")
                compacts.append({"r": len(reads), "u": len(usage), "q": len(prompts), "ts": ts,
                                 "trigger": trig if trig in ("auto", "manual") else "unknown",
                                 "pre_tokens": pre_tok if isinstance(pre_tok, int) and pre_tok > 0 else None})

    def turns_after(o):
        nxt = [b for b in boundaries if b > o]
        return max(0, (min(nxt) if nxt else turns) - o)

    # router suggestions and uptake
    router = []
    seen_sug = set()
    for h in hook_ctx:
        for sk, mark in _SKILL_LINE.findall(h["text"]):
            key = (h["tuid"], sk)
            if key in seen_sug:
                continue
            seen_sug.add(key)
            base = sk.split(":")[-1]
            idx = h["idx"]

            def hit(cl, base=base):
                if cl[1] == "Skill":
                    return cl[2].split(":")[-1] == base
                if cl[1] == "Read":
                    return cl[2].replace("\\", "/").lower().endswith("/%s/skill.md" % base.lower())
                return False
            trigger = calls[idx - 1][2] if 0 < idx <= len(calls) else ""
            window = calls[idx:idx + UPTAKE_WINDOW]
            router.append({"skill": base, "by": mark or sk.rpartition(":")[0], "ts": h["ts"],
                           "taken": any(hit(cl) for cl in window),
                           "prior": any(hit(cl) for cl in calls[:max(0, idx - 1)]),
                           "scratch": "scratchpad" in trigger.lower(),
                           "after": [cl[2][:FOLLOW_KEY_MAX] for cl in window if cl[1] in SHELLS and cl[2]]})

    # skill calls no suggestion led to: the agent picked the skill from its own list
    suggested_at = defaultdict(list)
    for h in hook_ctx:
        for sk, _ in _SKILL_LINE.findall(h["text"]):
            suggested_at[sk.split(":")[-1]].append(h["idx"])
    skill_self = []
    for i, cl in enumerate(calls):
        if cl[1] != "Skill" or not cl[2]:
            continue
        base = cl[2].split(":")[-1]
        if not any(j <= i < j + UPTAKE_WINDOW for j in suggested_at.get(base, ())):
            skill_self.append({"skill": cl[2], "ts": call_ts[i]})

    # interlock outcomes
    index = {cl[0]: i for i, cl in enumerate(calls)}
    interlock = []
    for b in blocks:
        i = index.get(b["tid"])
        if i is None:
            continue
        cmd = calls[i][2]
        after = calls[i + 1:i + 1 + INTERLOCK_WINDOW]
        blocked_later = {x["tid"] for x in blocks}
        outcome = "abandoned"
        listed = False
        first_word = (cmd.strip().split() or [""])[0].lower()
        for cl in after:
            if cl[1] in ("Glob", "Grep", "Read", "LS") or (cl[1] in SHELLS and _LISTING.match(cl[2])):
                listed = True
            if cl[1] in SHELLS and "# interlock-ok:" in cl[2]:
                outcome = "override_after_listing" if listed else "override_without_listing"
                break
            if cl[1] in SHELLS and cl[0] not in blocked_later and not _LISTING.match(cl[2]) and first_word and \
                    first_word in cl[2].lower().split():
                outcome = "rewritten"
                break
        nxt = next((p for p in prompt_calls if p[0] > i), None)
        user_ok = bool(nxt and nxt[0] <= i + 1 + INTERLOCK_WINDOW and nxt[1])
        shape = _cmd_shape(cmd)
        equivalent = bool(shape) and any(
            cl[1] in SHELLS and cl[0] not in blocked_later and "# interlock-ok:" not in cl[2]
            and _same_effect(shape, _cmd_shape(cl[2])) for cl in after)
        interlock.append({"ts": b["ts"], "outcome": outcome, "user_override": user_ok, "equivalent": equivalent})

    # palace precedent injections and later citations
    injected = {}
    for h in hook_ctx:
        for m in _INJECTED.finditer(h["text"]):
            injected.setdefault(m.group(1) + m.group(2), (h["ord"], h["event"] or "hook"))
    for i in prompt_ids:
        injected.setdefault(i, (0, "agent_prompt"))
    cited = 0
    by_channel: dict = defaultdict(lambda: [0, 0])
    for i, (o, ch) in injected.items():
        used = any(o <= co and i in ids for co, ids in cites)
        cited += used
        by_channel[ch][0] += 1
        by_channel[ch][1] += int(used)

    # output digest
    dg = []
    for r in digests:
        head = r.pop("text_head")
        full, source = None, "unknown"
        mp = _DIGEST_PATH.search(head)
        mf = digest_full.search(head)
        if mp:
            fp = Path(mp.group(1))
            try:
                full, source = fp.stat().st_size, "file"
            except OSError:
                if mf:
                    full, source = int(mf.group(1).replace(",", "")), "header"
        elif mf:
            full, source = int(mf.group(1).replace(",", "")), "header"
        elif "[%s]" % digest not in head:
            full, source = r["printed"], "passthrough"    # under the threshold: printed in full, nothing saved
        r.update({"full": full, "source": source, "turns_after": turns_after(r["ord"])})
        dg.append(r)

    seq = [u[0] + u[1] + u[2] for u in usage.values()]          # context per turn, by usage position
    cuts = sorted({p[0] for p in prompts} | {c["u"] for c in compacts})
    growth = []
    for pos, _ in prompts:
        nxt = [x for x in cuts if x > pos]
        seg = [x for x in seq[pos:(nxt[0] if nxt else len(seq))] if x > 0]
        start = next((x for x in reversed(seq[:pos]) if x > 0), None)
        if start is not None and seg and max(seg) >= start:
            growth.append(max(seg) - start)
    events = []
    for c in compacts:
        before, after = reads[:c["r"]], reads[c["r"]:c["r"] + REREAD_WINDOW]
        seen_before = set(before)
        prev, earlier = before[-REREAD_WINDOW:], set(before[:-REREAD_WINDOW])
        nxt_q = [p[1] for p in prompts[c["q"]:c["q"] + CORRECTION_PROMPTS]]
        at = c["pre_tokens"] or next((x for x in reversed(seq[:c["u"]]) if x > 0), None)
        events.append({"ts": c["ts"], "ctx": at, "trigger": c["trigger"],
                       "post_reread": round(sum(p in seen_before for p in after) / len(after), 3) if after else None,
                       "reads_after": len(after),
                       "pre_reread": round(sum(p in earlier for p in prev) / len(prev), 3)
                       if len(prev) >= PRE_MIN_READS else None,
                       "corrected": None if None in nxt_q else any(nxt_q), "prompts_after": len(nxt_q)})

    sums = [0, 0, 0, 0]
    ctx = [c for c in (u[0] + u[1] + u[2] for u in usage.values()) if c > 0]   # context per turn, in order
    for u in usage.values():
        for i in range(4):
            sums[i] += u[i]
    hooks_ts = [h["ts"] for h in hook_ctx]
    return {
        "v": SCAN_VERSION, "turns": turns, "boundaries": boundaries, "usage": sums, "ctx": ctx, "kept_chars": kept,
        "tool_calls": len(calls), "agent_calls": agent_calls, "agent_results": agent_results,
        "notes": list(notes.values()), "first_ts": first_ts, "last_ts": last_ts, "cwd": cwd,
        "long_lines": long_lines, "long_est": long_est, "router": router, "interlock": interlock,
        "correction": [h["ts"] for h in hook_ctx if CORRECTION_TEXT in h["text"]],
        "freshness": [h["ts"] for h in hook_ctx if FRESHNESS_TEXT in h["text"]],
        "palace": {"injected": len(injected), "cited": cited,
                   "by_channel": {k: list(v) for k, v in by_channel.items()},
                   "ts": hooks_ts[0] if hooks_ts else first_ts},
        "digests": dg, "prompts": len(prompts), "growth": growth, "compactions": events,
        "skill_calls": [cl[2] for cl in calls if cl[1] == "Skill" and cl[2]], "skill_self": skill_self,
        "correction_fired": fired, "correction_checked": sum(p[1] is not None for p in prompts),
        "hook_runs": hook_runs, "gates": gates, "title": title or ai_title,
        "turn_usage": [[mid, meta[0], meta[1]] + list(usage.get(mid, [0, 0, 0, 0])) + [meta[2], meta[3]]
                       for mid, meta in turn_meta.items()],
        "turn_acts": turn_acts, "call_mid": call_mid, "proc_steps": proc_steps,
        "prompt_flags": prompt_flags, "call_keys": call_keys, "reject_turns": reject_turns,
    }


_TRIVIAL = {"cd", "pushd", "popd", "export", "set", "echo", "printf", "true", "sleep", "tail", "head", "grep",
            "wc", "sort", "cat", "set-location"}


def _seg_shape(seg: str):
    """(program, flags, targets) of one command segment: combined short flags split (-rf = -r -f), quotes,
    redirections, a leading ./ and trailing slashes dropped. None for a segment that changes nothing."""
    toks = [t.strip("\"'") for t in seg.split()]
    while toks and re.match(r"^[A-Za-z_]\w*=", toks[0]):          # VAR=value prefixes
        toks = toks[1:]
    if not toks or toks[0].lower() in _TRIVIAL or _LISTING.match(" ".join(toks)):
        return None
    flags, targets = set(), set()
    for t in toks[1:]:
        if not t or "<" in t or ">" in t:
            continue
        if re.fullmatch(r"-[A-Za-z]{2,}", t):
            flags.update("-" + c for c in t[1:])
        elif t.startswith("-"):
            flags.add(t.lower())
        else:
            x = t.replace("\\", "/")
            x = x[2:] if x.startswith("./") else x
            targets.add(x.rstrip("/").lower() or x)
    return (toks[0].lower(), frozenset(flags), frozenset(targets))


def _cmd_shape(cmd: str) -> list:
    """The shapes of a shell command's segments (split on && || ; | and newlines; a # comment ends it),
    leaving out segments that only move around, print or filter."""
    body = re.split(r"(?:^|\s)#", cmd or "", maxsplit=1)[0]
    return [s for s in (_seg_shape(x) for x in re.split(r"&&|\|\||;|\||\n", body)) if s]


def _same_effect(blocked: list, later: list) -> bool:
    """Every segment of the blocked command reappears in the later one with the same program and targets
    and every flag kept: the same action phrased so that it got through. Dropping a flag (git push without
    --force), narrowing the target (one file instead of the folder) or dropping the destructive step is a
    safer command, not an equivalent one."""
    return bool(blocked) and all(any(l[0] == b[0] and l[2] == b[2] and b[1] <= l[1] for l in later)
                                 for b in blocked)


def turns_after(summary: dict, o: int) -> int:
    nxt = [b for b in summary.get("boundaries") or [] if b > o]
    return max(0, (min(nxt) if nxt else summary["turns"]) - o)


# ---------------------------------------------------------------------------
# discovery and cache
# ---------------------------------------------------------------------------

def discover(projects: Path, cutoff: float):
    """{session_dir_key: {"main": Path|None, "subs": [Path], "project": str}} for sessions touched since cutoff."""
    sessions: dict = {}
    if not projects.is_dir():
        return sessions
    for pdir in projects.iterdir():
        if not pdir.is_dir():
            continue
        for f in pdir.glob("*.jsonl"):
            try:
                if f.stat().st_mtime >= cutoff:
                    sessions.setdefault(str(pdir / f.stem), {"main": None, "subs": [], "project": pdir.name})["main"] = f
            except OSError:
                pass
        # subagents/agent-*.jsonl, and workflow steps under subagents/workflows/<run>/
        for f in pdir.glob("*/subagents/**/agent-*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            sdir = next(p for p in f.parents if p.name == "subagents").parent
            s = sessions.setdefault(str(sdir), {"main": None, "subs": [], "project": pdir.name})
            s["subs"].append(f)
            main = sdir.with_suffix(".jsonl")
            if s["main"] is None and main.exists():
                s["main"] = main
    return sessions


class Cache:
    def __init__(self, path: Path | None):
        self.path = path
        self.old: dict = {}
        self.new: dict = {}
        self.hits = self.misses = 0
        if path and path.exists():
            try:
                self.old = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.old = {}

    def get(self, f: Path, line_cap: int, digest: str = "") -> dict:
        st = f.stat()
        key = "%d:%d:%d:%d:%s" % (st.st_size, st.st_mtime_ns, line_cap, SCAN_VERSION, digest)
        c = self.old.get(str(f))
        if c and c.get("k") == key:
            self.hits += 1
            self.new[str(f)] = c
            return c["v"]
        self.misses += 1
        v = scan_file(f, line_cap, digest)
        self.new[str(f)] = {"k": key, "v": v}
        return v

    def save(self):
        if not self.path:
            return
        keep = {k: v for k, v in self.old.items() if k not in self.new and os.path.exists(k)}
        keep.update(self.new)
        try:
            write_private(self.path, json.dumps(keep))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

def _week(ts: str) -> str:
    try:
        d = dt.datetime.fromisoformat(ts[:19])          # "2030-01-02T03:04:05" (UTC); fractions and zone dropped
    except ValueError:
        return "unknown"
    y, w, _ = d.isocalendar()
    return "%d-W%02d" % (y, w)


def _in_window(ts: str, cutoff_iso: str) -> bool:
    return not ts or ts >= cutoff_iso


def _read_json(p: Path, default=None, max_bytes: int | None = None):
    try:
        if max_bytes is not None and p.stat().st_size > max_bytes:
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return default


def write_private(path: Path, text: str) -> None:
    """Write text to path readable by this account only: a new folder is made 0700, the file is written
    under a fresh mkstemp name (0600, never an existing file or a planted link) and moved into place."""
    path = Path(path)
    if not path.parent.is_dir():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _project_label(summary: dict | None, fallback: str) -> str:
    cwd = (summary or {}).get("cwd") or ""
    name = re.split(r"[\\/]", cwd.rstrip("\\/"))[-1] if cwd else ""
    return name or fallback


def dominant_model(S: dict) -> str:
    """The model most of a transcript's assistant messages ran on ("" when none is recorded)."""
    c = _count(m for _mid, _ts, m, *_rest in S.get("turn_usage") or [] if m and not m.startswith("<"))
    return max(c.items(), key=lambda kv: (kv[1], kv[0]))[0] if c else ""


def _call_id(k) -> tuple:
    return ("sh" if k[0] in SHELLS else k[0], k[1])


def spawn_signals(S: dict, P: dict, ci: int, pi: int, ret_ord: int) -> dict:
    """Accuracy SIGNALS for one linked spawn, each True / False, or None when it can't be measured.
    S: the subagent's summary, P: its parent's. ci / pi: the parent's tool-call and prompt counts when the
    result came back; ret_ord: the parent turn it came back after."""
    mine = {_call_id(k) for k in S.get("call_keys") or [] if len(k[1]) >= 6}
    after = [k for k in P.get("call_keys") or [] if ci <= k[2] < ci + REDO_CALLS]
    redone = None
    if mine and P.get("tool_calls", 0) > ci:
        redone = any(_call_id(k) in mine for k in after)
    flags = (P.get("prompt_flags") or [])[pi:pi + CORRECTED_PROMPTS]
    corrected = None
    if flags and None not in flags:
        corrected = any(flags)
    rejected = None
    if P.get("turns", 0) > ret_ord:
        rejected = any(ret_ord < t <= ret_ord + REJECT_TURNS for t in P.get("reject_turns") or [])
    return {"redone": redone, "corrected": corrected, "rejected": rejected}


SIGNALS = ("redone", "corrected", "rejected")
SIGNAL_MISSES = {
    "redone": "a redo that reads other files; a parent that trusts a wrong answer; re-reads made only to edit "
              "(counted as redone)",
    "corrected": "a correction about something else; a bad result nobody noticed; spawns whose parent has no "
                 "typed prompts after the return",
    "rejected": "any complaint not phrased as 'the agent / its result ... wrong / incomplete / made up': "
                "the pattern is narrow and under-reports",
}


def subagent_rows(sessions: dict, summaries: dict, cutoff_iso: str) -> tuple[list, int]:
    rows, unlinked_out = [], 0
    for skey, s in sessions.items():
        files = ([s["main"]] if s["main"] else []) + s["subs"]
        sm = {str(f): summaries[str(f)] for f in files if str(f) in summaries}
        by_tid = {}
        for fk, v in sm.items():
            for tid in v["agent_calls"]:
                by_tid[tid] = fk
        main_sum = summaries.get(str(s["main"])) if s["main"] else None
        project = _project_label(main_sum, s["project"])
        for f in s["subs"]:
            S = sm.get(str(f))
            if S is None:
                continue
            aid = f.stem[len("agent-"):] if f.stem.startswith("agent-") else f.stem
            meta = _read_json(f.with_name(f.stem + ".meta.json"), {}) or {}
            tid = meta.get("toolUseId") or ""
            pk = by_tid.get(tid)
            if not pk:                                   # no meta link: find the launch by agent id
                for fk, v in sm.items():
                    for t2, r in v["agent_results"].items():
                        if r.get("aid") == aid and t2 in v["agent_calls"]:
                            pk, tid = fk, t2
            u = S["usage"]
            spend = {"input": u[0], "cache_creation": u[1], "cache_read": u[2], "output": u[3], "total": sum(u)}
            kept = est(S["kept_chars"])
            atype = meta.get("agentType") or (sm[pk]["agent_calls"][tid]["type"] if pk else "") or "unknown"
            row = {"agent": aid, "type": atype, "project": project, "ts": S["first_ts"],
                   "week": _week(S["first_ts"]), "spend": spend, "kept_out": kept,
                   "tool_calls": S["tool_calls"], "depth": meta.get("spawnDepth") or 1,
                   "fork": atype == "fork" or bool(meta.get("isFork") or meta.get("forked")),
                   "background": meta.get("requestShape") == "background", "linked": bool(pk),
                   "desc": str(meta.get("description") or "")[:DESC_MAX], "long_est": S["long_est"],
                   "model": dominant_model(S) or str(meta.get("model") or "") or (
                       sm[pk]["agent_calls"][tid].get("model", "") if pk else "") or "unknown",
                   "effort": str(meta.get("effort") or "") or None}
            row.update({k: None for k in SIGNALS})
            if not pk:
                row.update({"returned": None, "net_kept_out": None, "parent_turns_after": None,
                            "reads_avoided": None, "worth_it": None, "small": None,
                            "why_unlinked": "workflow" if meta.get("workflowPhase") or atype == "workflow-subagent"
                            else "parent transcript not found"})
                if _in_window(row["ts"], cutoff_iso):
                    rows.append(row)
                else:
                    unlinked_out += 1
                continue
            P = sm[pk]
            call = P["agent_calls"][tid]
            row["ts"] = call.get("ts") or row["ts"]
            row["week"] = _week(row["ts"])
            row["desc"] = row["desc"] or call.get("desc", "")
            res = P["agent_results"].get(tid) or {}
            ret_chars = res.get("chars", 0)
            ret_ord = res.get("ord", call["ord"])
            ci, pi = res.get("ci", 0), res.get("pi", 0)
            notif_tokens = None
            if res.get("async") or row["background"]:
                row["background"] = True
                mine = [n for n in P["notes"] if (n["tid"] and n["tid"] == tid) or n["aid"] == aid]
                ret_chars += sum(n["chars"] for n in mine)
                with_result = [n["ord"] for n in mine if n["result"]] or [n["ord"] for n in mine]
                if with_result:
                    ret_ord = min(with_result)
                    first = min((n for n in mine if n["ord"] == ret_ord), key=lambda n: n.get("ci", 0))
                    ci, pi = first.get("ci", ci), first.get("pi", pi)
                toks = [n["tokens"] for n in mine if n["tokens"]]
                notif_tokens = max(toks) if toks else None
                row["return_seen"] = bool(mine)
            else:
                row["return_seen"] = bool(res)
            returned = est(ret_chars)
            net = kept - returned
            after = turns_after(P, ret_ord)
            avoided = net * after
            small = S["tool_calls"] <= SMALL_TOOL_CALLS or kept < SMALL_KEPT_TOKENS
            row.update({"returned": returned, "net_kept_out": net, "parent_turns_after": after,
                        "reads_avoided": avoided, "worth_it": avoided > spend["total"], "small": small,
                        "notified_tokens": notif_tokens,
                        "parent": "session" if s["main"] and pk == str(s["main"]) else "subagent"})
            if row["return_seen"]:
                row.update(spawn_signals(S, P, ci, pi, ret_ord))
            if _in_window(row["ts"], cutoff_iso):
                rows.append(row)
    return rows, unlinked_out


def _group(rows: list, key: str) -> dict:
    g: dict = {}
    for r in rows:
        k = r.get(key) or "unknown"
        a = g.setdefault(k, {"spawns": 0, "linked": 0, "worth_it": 0, "small": 0, "spend": 0, "linked_spend": 0,
                             "kept_out": 0, "returned": 0, "net_kept_out": 0, "reads_avoided": 0})
        a["spawns"] += 1
        a["spend"] += r["spend"]["total"]
        a["kept_out"] += r["kept_out"]
        if r["linked"]:
            a["linked"] += 1
            a["linked_spend"] += r["spend"]["total"]
            a["worth_it"] += int(bool(r["worth_it"]))
            a["small"] += int(bool(r["small"]))
            for k2 in ("returned", "net_kept_out", "reads_avoided"):
                a[k2] += r[k2]
        for sg in SIGNALS:
            if r.get(sg) is not None:
                a[sg] = a.get(sg, 0) + int(r[sg])
                a[sg + "_n"] = a.get(sg + "_n", 0) + 1
    for a in g.values():
        a["worth_it_share"] = round(a["worth_it"] / a["linked"], 3) if a["linked"] else None
        a["signals"] = {sg: signal_rate(a.get(sg, 0), a.get(sg + "_n", 0)) for sg in SIGNALS}
    return dict(sorted(g.items(), key=lambda kv: -kv[1]["spend"]))


def signal_rate(hits: int, n: int) -> dict:
    """A SIGNAL as a rate with its sample size; `rate` is None ("n/a") when nothing was measurable."""
    return {"hits": hits, "n": n, "rate": round(hits / n, 3) if n else None, "thin": 0 < n < THIN_N}


def dispatched_sessions(gk: Path, sessions: dict, summaries: dict, cutoff_iso: str) -> dict:
    """Sessions session-dispatch started, from its roles.json read as plain data (core imports no interjector
    code). Each row: who, model / effort, whether it recorded a pull request and whether that merged, and its
    user corrections per typed prompt. CI results are not recorded by dispatch, so they are not reported."""
    store = _read_json(gk / "dispatch" / "roles.json", {})
    store = store if isinstance(store, dict) else {}
    mains = {s["main"].stem: str(s["main"]) for s in sessions.values() if s["main"]}
    rows = []
    for key, rec in store.items():
        if not isinstance(rec, dict) or not _in_window(str(rec.get("started") or ""), cutoff_iso):
            continue
        S = summaries.get(mains.get(str(rec.get("sessionId") or "")) or "")
        flags = (S or {}).get("prompt_flags") or []
        flags = flags[1:]                       # the first prompt is the fixed sentence that names the brief
        checked = [f for f in flags if f is not None]
        rows.append({
            "key": key, "name": rec.get("name"), "role": rec.get("role") or "unknown", "parent": rec.get("parent"),
            "project": re.split(r"[\\/]", str(rec.get("project") or "").rstrip("\\/"))[-1] or "unknown",
            "started": rec.get("started"), "done": bool(rec.get("done")),
            "model": str(rec.get("model") or "") or (dominant_model(S) if S else "") or "unknown",
            "effort": str(rec.get("effort") or "") or None,
            "pr": rec.get("pr"), "merged": bool(rec.get("merged")),
            "transcript_found": S is not None,
            "prompts": len(checked), "corrections": sum(bool(f) for f in checked),
            "spend": sum(S["usage"]) if S else None,
        })

    def agg(group: list) -> dict:
        with_t = [r for r in group if r["transcript_found"]]
        prompts, corr = sum(r["prompts"] for r in with_t), sum(r["corrections"] for r in with_t)
        with_pr = [r for r in group if r["pr"]]
        return {"sessions": len(group), "with_pr": len(with_pr), "merged": sum(r["merged"] for r in with_pr),
                "pr_merged": signal_rate(sum(r["merged"] for r in with_pr), len(with_pr)),
                "corrections_per_prompt": signal_rate(corr, prompts), "transcripts": len(with_t),
                "spend": sum(r["spend"] or 0 for r in with_t)}

    def by(field: str) -> dict:
        keys = sorted({r[field] for r in rows})
        return {k: agg([r for r in rows if r[field] == k]) for k in keys}

    return {"label": "SIGNAL", "sessions": len(rows), "total": agg(rows), "by_role": by("role"),
            "by_model": by("model"), "rows": sorted(rows, key=lambda r: str(r["started"] or "")),
            "not_recorded": ["ci_before_merge"]}


def _mark_weeks(weeks: dict, cutoff_iso: str, now: float) -> dict:
    """Each ISO week's days in the window [cutoff, now], and whether the window covers only part of it."""
    t0 = dt.datetime.fromisoformat(cutoff_iso[:19]).replace(tzinfo=dt.timezone.utc).timestamp()
    for k, a in weeks.items():
        try:
            y, w = k.split("-W")
            start = dt.datetime.combine(dt.date.fromisocalendar(int(y), int(w), 1), dt.time(),
                                        dt.timezone.utc).timestamp()
        except ValueError:                             # "unknown": a spawn with no readable start
            a["days_in_window"] = a["partial"] = None
            continue
        end = start + 7 * 86400
        a["days_in_window"] = round(max(0.0, min(end, now) - max(start, t0)) / 86400, 1)
        a["partial"] = start < t0 or end > now
    return weeks


def main_context(sessions: dict, summaries: dict, cutoff_iso: str, budget: int) -> dict:
    """Context per main-session assistant turn (input + cache read + cache creation), subagents left out."""
    per, all_ctx = [], []
    compacted = compactions = 0
    for skey, s in sessions.items():
        f = s["main"]
        v = summaries.get(str(f)) if f else None
        if not v or not v.get("ctx") or not _in_window(v.get("last_ts") or "", cutoff_iso):
            continue
        c = v["ctx"]
        all_ctx += c
        nb = len(v.get("boundaries") or [])
        compacted += int(nb > 0)
        compactions += nb
        per.append({"session": Path(skey).name, "project": _project_label(v, s["project"]), "ts": v["first_ts"],
                    "turns": len(c), "peak": max(c), "reread": sum(c), "compactions": nb,
                    "reread_capped": sum(min(x, budget) for x in c)})
    total = sum(all_ctx)
    over = {}
    for t in CONTEXT_THRESHOLDS:
        hi = [x for x in all_ctx if x > t]
        over[str(t)] = {"turns_share": round(len(hi) / len(all_ctx), 3) if all_ctx else None,
                        "reread_share": round(sum(hi) / total, 3) if total else None}
    capped = sum(min(x, budget) for x in all_ctx)
    srt = sorted(all_ctx)
    return {
        "budget": budget, "sessions": len(per), "turns": len(all_ctx), "reread": total,
        "reread_capped": capped, "reread_over_budget": total - capped,
        "peak": srt[-1] if srt else None, "median": srt[len(srt) // 2] if srt else None,
        "over": over, "compacted_sessions": compacted, "compactions": compactions,
        "labels": {"turns": "MEASURED", "reread": "MEASURED", "peak": "MEASURED", "median": "MEASURED",
                   "over": "MEASURED", "compacted_sessions": "MEASURED", "reread_capped": "ESTIMATED",
                   "reread_over_budget": "ESTIMATED"},
        "per_session": sorted(per, key=lambda r: -r["reread"]),
    }


def _p90(xs: list):
    s = sorted(xs)
    return s[int(0.9 * (len(s) - 1))] if s else None


def _mean(xs: list):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def compaction_stats(sessions: dict, summaries: dict, cutoff_iso: str) -> dict:
    """Per project (transcript folder name): base, growth per prompt, and every compaction with its harm signals."""
    acc: dict = {}
    for s in sessions.values():
        f = s["main"]
        v = summaries.get(str(f)) if f else None
        if not v or not v.get("ctx") or not _in_window(v.get("last_ts") or "", cutoff_iso):
            continue
        a = acc.setdefault(s["project"], {"sessions": 0, "turns": 0, "prompts": 0, "base": [], "growth": [],
                                          "peaks": [], "compacted": 0, "events": [], "cwds": defaultdict(int)})
        a["sessions"] += 1
        a["turns"] += len(v["ctx"])
        a["prompts"] += v.get("prompts") or 0
        if len(v["ctx"]) >= MIN_SESSION_TURNS:
            a["base"].append(min(v["ctx"][:BASE_TURNS]))
        a["growth"] += v.get("growth") or []
        a["peaks"].append(max(v["ctx"]))
        ev = v.get("compactions") or []
        a["compacted"] += int(bool(ev))
        a["events"] += ev
        if v.get("cwd"):
            a["cwds"][v["cwd"]] += 1
    out = {}
    for name, a in sorted(acc.items()):
        ev = a["events"]
        corr = [e["corrected"] for e in ev if e["corrected"] is not None]
        ctxs = [e["ctx"] for e in ev if e["ctx"]]
        out[name] = {
            "cwd": max(a["cwds"], key=a["cwds"].get) if a["cwds"] else None,
            "sessions": a["sessions"], "turns": a["turns"], "prompts": a["prompts"],
            "base": int(statistics.median(a["base"])) if a["base"] else None,
            "growth_samples": len(a["growth"]),
            "growth_median": int(statistics.median(a["growth"])) if a["growth"] else None,
            "growth_p90": _p90(a["growth"]),
            "peaks": a["peaks"], "compacted_sessions": a["compacted"],
            "compaction": {
                "count": len(ev), "auto": sum(e["trigger"] == "auto" for e in ev),
                "manual": sum(e["trigger"] == "manual" for e in ev),
                "unknown": sum(e["trigger"] == "unknown" for e in ev),
                "ctx_median": int(statistics.median(ctxs)) if ctxs else None,
                "measured": sum(e["post_reread"] is not None for e in ev),
                "reread_pre": _mean([e["pre_reread"] for e in ev]),
                "reread_post": _mean([e["post_reread"] for e in ev]),
                "corrected": sum(corr), "correction_checked": len(corr),
                "events": sorted(ev, key=lambda e: e["ts"] or "")[-50:],
            },
            "labels": {"base": "MEASURED", "growth": "MEASURED", "compaction": "MEASURED"},
        }
    return out


def project_budget(cwd: str | None, default: int) -> tuple:
    """The budget a project runs with now: env.GNOMONKIT_CONTEXT_BUDGET in its settings, else the global one."""
    if cwd:
        for fn in ("settings.local.json", "settings.json"):
            d = _read_json(Path(cwd) / ".claude" / fn, {}, max_bytes=SETTINGS_MAX_BYTES)
            env = d.get("env") if isinstance(d, dict) else None
            v = budget_value(env.get("GNOMONKIT_CONTEXT_BUDGET")) if isinstance(env, dict) else None
            if v:
                return v, fn
    return default, "global"


def _k(x) -> str:
    return "%dk" % round(x / 1000)


def confidence(sessions: int, prompts: int) -> str:
    if sessions >= CONFIDENCE_HIGH[0] and prompts >= CONFIDENCE_HIGH[1]:
        return "high"
    if sessions >= CONFIDENCE_MEDIUM[0] and prompts >= CONFIDENCE_MEDIUM[1]:
        return "medium"
    return "low"


def recommend_budget(p: dict, current: int) -> dict:
    """One project's suggested budget from its compaction_stats entry, with a confidence and a one-line reason."""
    windows = p["sessions"] + p["compaction"]["count"]      # each compaction starts a fresh context window
    conf = confidence(windows, p["prompts"])
    if conf == "low" or p["base"] is None or p["growth_p90"] is None:
        return {"current": current, "recommended": current, "change": 0, "confidence": "low", "start": None,
                "rule": "low_sample", "reason": "too little evidence (%d context windows, %d prompts); kept at %s" % (
                    windows, p["prompts"], _k(current))}
    start = max(BUDGET_MIN, min(BUDGET_MAX, -(-(p["base"] + p["growth_p90"]) // BUDGET_STEP) * BUDGET_STEP))
    c = p["compaction"]
    pre, post = c["reread_pre"], c["reread_post"]
    excess = post - pre if pre is not None and post is not None else None
    corr_share = c["corrected"] / c["correction_checked"] if c["correction_checked"] else 0.0
    evidence = c["measured"] >= HARM_MIN_COMPACTIONS
    harmful = evidence and ((excess is not None and excess >= HARM_MARGIN) or corr_share >= CORRECTION_HARM_SHARE)
    over = sum(x > current for x in p["peaks"]) / len(p["peaks"]) if p["peaks"] else 0.0
    rare = p["compacted_sessions"] / p["sessions"] < RARE_COMPACTION_SHARE if p["sessions"] else False
    basis = "base %s + p90 growth %s" % (_k(p["base"]), _k(p["growth_p90"]))
    if harmful:
        rec, rule = start + BUDGET_STEP, "raise_harm"
        why = "re-read %d%% after vs %d%% before" % (round(100 * post), round(100 * pre))             if excess is not None and excess >= HARM_MARGIN else "corrections after %d%%" % round(100 * corr_share)
        reason = "%s; %d compactions show harm (%s); one step up" % (basis, c["measured"], why)
    elif evidence and over >= OVER_BUDGET_SHARE:
        rec, rule = start - BUDGET_STEP, "lower_harmless"
        reason = "%s; %d%% of sessions run past %s and %d compactions show no harm; one step down" % (
            basis, round(100 * over), _k(current), c["measured"])
    elif rare and p["growth_p90"] < SMALL_GROWTH:
        rec, rule = start - BUDGET_STEP, "lower_rare"
        reason = "%s; compaction is rare and tasks are small; one step down" % basis
    else:
        rec, rule = start, "base_growth"
        reason = basis
    clamped = max(BUDGET_MIN, min(BUDGET_MAX, rec))
    if clamped != rec:
        reason += "; clamped to %s" % _k(clamped)
    return {"current": current, "recommended": clamped, "change": clamped - current, "confidence": conf,
            "start": start, "rule": rule, "reason": reason, "over_budget_share": round(over, 3),
            "reread_excess": round(excess, 3) if excess is not None else None,
            "correction_share": round(corr_share, 3)}


def budgets(comp: dict, default: int, generated_at: str = "", since_days: float = 0) -> dict:
    rows = []
    for name, p in comp.items():
        cur, src = project_budget(p["cwd"], default)
        r = {"project": name, "cwd": p["cwd"], "source": src, "sessions": p["sessions"], "prompts": p["prompts"],
             "base": p["base"], "growth_median": p["growth_median"], "growth_p90": p["growth_p90"],
             "compactions": p["compaction"]["count"]}
        r.update(recommend_budget(p, cur))
        rows.append(r)
    return {"schema": 1, "generated_at": generated_at, "since_days": since_days, "default_budget": default,
            "step": BUDGET_STEP, "min": BUDGET_MIN, "max": BUDGET_MAX,
            "label": "ESTIMATED (a recommendation from MEASURED inputs)", "projects": rows}


# ---------------------------------------------------------------------------
# launch readiness: one row per metric, each against a target
# ---------------------------------------------------------------------------

# Targets for the readiness rows. "higher": True means bigger is better. ok / warn are the bounds for
# those statuses (anything worse is fail); below min_n samples a row is no-data, with its value still
# shown. These are starting points to tune, not measured facts: change them here, in one place.
READINESS_TARGETS = {
    "skill_uptake":           {"higher": True,  "ok": 0.50, "warn": 0.30, "min_n": 10, "unit": "share"},
    "interlock_false_blocks": {"higher": False, "ok": 0.20, "warn": 0.35, "min_n": 5,  "unit": "share"},
    "correction_precision":   {"higher": True,  "ok": 0.80, "warn": 0.60, "min_n": 10, "unit": "share"},
    "hook_latency_p95":       {"higher": False, "ok": 1000, "warn": 3000, "min_n": 20, "unit": "ms"},
    "hook_errors":            {"higher": False, "ok": 0.01, "warn": 0.05, "min_n": 20, "unit": "share"},
    "bench_applied":          {"higher": True,  "ok": 0.80, "warn": 0.60, "min_n": 3,  "unit": "share"},
    "leak_gate_catches":      {"higher": False, "ok": 0.10, "warn": 0.25, "min_n": 5,  "unit": "share"},
}
BENCH_ARM = "gnomonkit"           # the bench arm whose applied rate is the readiness score


def readiness_status(value, n: int, t: dict) -> str:
    if value is None or not n or n < t["min_n"]:
        return "no-data"
    if t["higher"]:
        return "ok" if value >= t["ok"] else "warn" if value >= t["warn"] else "fail"
    return "ok" if value <= t["ok"] else "warn" if value <= t["warn"] else "fail"


def _target_text(t: dict) -> str:
    op = ">=" if t["higher"] else "<="
    if t["unit"] == "ms":
        return "%s %d ms" % (op, t["ok"])
    return "%s %d%%" % (op, round(100 * t["ok"]))


def _row(rid: str, metric: str, value, n: int, label: str, definition: str, detail: dict | None = None,
         note: str = "") -> dict:
    t = READINESS_TARGETS[rid]
    st = readiness_status(value, n, t)
    if not note and st == "no-data":
        note = "no samples" if not n else "fewer than %d samples" % t["min_n"]
    if t["unit"] == "share":
        shown = "-" if value is None else "%d%%" % round(100 * value)
    else:
        shown = "-" if value is None else "%d ms" % round(value)
    return {"id": rid, "metric": metric, "value": None if value is None else round(value, 3), "display": shown,
            "n": n, "target": _target_text(t), "target_ok": t["ok"], "target_warn": t["warn"],
            "higher_is_better": t["higher"], "min_n": t["min_n"], "status": st, "label": label,
            "definition": definition, "note": note, "detail": detail or {}}


def core_skills() -> set:
    root = Path(__file__).resolve().parents[1]
    try:
        return {d.name for d in root.iterdir() if (d / "SKILL.md").is_file()}
    except OSError:
        return set()


def core_hook_scripts() -> set:
    root = Path(__file__).resolve().parents[2] / "hooks"
    try:
        return {f.name for f in root.glob("*.py") if not f.name.startswith(("test_", "build_"))}
    except OSError:
        return set()


def trigger_files(home: Path) -> list:
    """Every router rule file to read follow_through patterns from: core's own, the paths in
    GNOMONKIT_TRIGGER_FILES, then the newest installed copy of each other plugin that ships a router."""
    files = [Path(__file__).resolve().parents[2] / "hooks" / "triggers.json"]
    files += [Path(p) for p in (os.environ.get("GNOMONKIT_TRIGGER_FILES") or "").split(os.pathsep) if p]
    try:
        for plugin in sorted((home / "plugins" / "cache").glob("*/*")):
            found = [f for f in plugin.glob("*/hooks/triggers.json") if f.is_file()]
            if found:
                files.append(max(found, key=lambda f: f.stat().st_mtime))
    except OSError:
        pass
    return files


def follow_patterns(files: list) -> dict:
    """{skill: [compiled follow_through regexes]}. The first file to name a skill wins; a bad file
    or pattern is skipped, so a broken overlay never breaks the report."""
    out: dict = {}
    for f in files:
        try:
            rules = (json.loads(Path(f).read_text(encoding="utf-8")) or {}).get("triggers") or []
        except (OSError, ValueError, AttributeError):
            continue
        for r in rules:
            if not isinstance(r, dict) or not r.get("follow_through"):
                continue
            base = str(r.get("skill") or "").split(":")[-1]
            pats = []
            for p in r["follow_through"]:
                try:
                    pats.append(re.compile(str(p), re.I))
                except re.error:
                    pass
            if base and pats and base not in out:
                out[base] = pats
    return out


def _core_skill(key: str, skills: set) -> bool:
    plug, _, base = key.rpartition(":")
    return base in skills and plug in ("", "core")


def bench_root() -> Path:
    env = os.environ.get("GNOMONKIT_BENCH_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "gnomonkit" / "bench"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "gnomonkit" / "bench"


def latest_bench(root: Path):
    """The newest results.json one level under the bench output root (bench writes <root>/<round>/results.json)."""
    best = None
    try:
        for f in root.glob("*/results.json"):
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue
            if best is None or mt > best[0]:
                best = (mt, f)
    except OSError:
        return None
    return best[1] if best else None


def bench_cells(records: list) -> list:
    """k/n applied per (scenario, arm); infrastructure errors are left out of n, as bench's own tally does."""
    t: dict = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        key = (str(r.get("family") or ""), str(r.get("scenario") or ""), str(r.get("arm") or ""))
        c = t.setdefault(key, {"family": key[0], "scenario": key[1], "arm": key[2],
                               "polarity": r.get("polarity"), "k": 0, "n": 0, "errors": 0})
        if r.get("error") and r.get("error_kind") != "behaviour":
            c["errors"] += 1
            continue
        c["n"] += 1
        c["k"] += 1 if r.get("applied") is True else 0
    for c in t.values():
        c["rate"] = round(c["k"] / c["n"], 3) if c["n"] else None
    return sorted(t.values(), key=lambda c: (c["family"], c["scenario"], c["arm"]))


def _sample_key(session: str, ts: str, text: str) -> str:
    return hashlib.sha1(("%s|%s|%s" % (session, ts, text)).encode("utf-8", "replace")).hexdigest()[:12]


_YES = ("yes", "y", "correction", "true", "1")
_NO = ("no", "n", "not", "false", "0")


def correction_sample(path: Path | None, fires: list) -> dict:
    """Merge this window's fires into the hand-labelling file, keeping every labelled row; read the labels."""
    rows: dict = {}
    if path and path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("key"):
                    rows[str(r["key"])] = r
        except OSError:
            pass
    for f in sorted(fires, key=lambda f: f["ts"] or "", reverse=True):
        k = _sample_key(f["session"], f["ts"], f["text"])
        if k not in rows:
            rows[k] = {"key": k, "session": f["session"], "ts": f["ts"], "strict": f["strict"], "label": "",
                       "text": f["text"]}
    labelled = [r for r in rows.values() if str(r.get("label") or "").strip().lower() in _YES + _NO]
    unlabelled = sorted((r for r in rows.values() if r not in labelled), key=lambda r: r.get("ts") or "",
                        reverse=True)
    keep = labelled + unlabelled[:max(0, SAMPLE_MAX - len(labelled))]
    if path:
        try:
            write_private(path, "".join(json.dumps(r, ensure_ascii=False) + "\n"
                                        for r in sorted(keep, key=lambda r: r.get("ts") or "")))
        except OSError:
            pass
    yes = sum(str(r["label"]).strip().lower() in _YES for r in labelled)
    return {"path": str(path) if path else None, "rows": len(keep), "labelled": len(labelled), "yes": yes,
            "no": len(labelled) - yes}


def readiness(sessions: dict, summaries: dict, cutoff_iso: str, sample_path: Path | None = None,
              bench_dir: Path | None = None) -> dict:
    rows = []
    skills = core_skills()
    hooks_core = core_hook_scripts()

    # skill uptake, per session
    suggested = invoked = any_core = total = 0
    sug_n = sug_taken = sug_prior = 0
    for skey, s in sessions.items():
        vs = [summaries[str(f)] for f in ([s["main"]] if s["main"] else []) + s["subs"] if str(f) in summaries]
        vs = [v for v in vs if _in_window(v.get("last_ts") or "", cutoff_iso)]
        if not vs:
            continue
        total += 1
        sug = [r for v in vs for r in v.get("router") or [] if r["skill"] in skills and _in_window(r["ts"], cutoff_iso)]
        used = any(_core_skill(k, skills) for v in vs for k in v.get("skill_calls") or [])
        any_core += int(used)
        sug_n += len(sug)
        sug_taken += sum(bool(r["taken"]) for r in sug)
        sug_prior += sum(bool(r.get("prior")) and not r["taken"] for r in sug)
        if sug:
            suggested += 1
            invoked += int(used)
    sug_open = sug_n - sug_prior
    rows.append(_row(
        "skill_uptake", "Skill uptake", sug_taken / sug_open if sug_open else None, sug_open, "MEASURED",
        "Of the skill router's suggestions of a core skill, the share taken up: a Skill call for that skill, "
        "or a Read of its SKILL.md, within %d tool calls of the suggestion. A suggestion of a skill already "
        "loaded earlier is left out (suggestions_already_loaded): loading it again changes nothing."
        % UPTAKE_WINDOW,
        {"suggestions": sug_n, "suggestions_open": sug_open, "suggestions_taken": sug_taken,
         "suggestions_already_loaded": sug_prior,
         "sessions": total, "sessions_suggested": suggested, "sessions_invoked_after_suggestion": invoked,
         "sessions_with_core_skill": any_core}))

    # interlock false blocks
    blocks = [i for v in summaries.values() for i in v.get("interlock") or [] if _in_window(i["ts"], cutoff_iso)]
    rewritten = sum(i["outcome"] == "rewritten" for i in blocks)
    user_ok = sum(bool(i.get("user_override")) for i in blocks)
    equivalent = sum(bool(i.get("equivalent")) for i in blocks)
    false = sum(bool(i.get("equivalent")) or bool(i.get("user_override")) for i in blocks)
    rows.append(_row(
        "interlock_false_blocks", "Interlock false blocks", false / len(blocks) if blocks else None, len(blocks),
        "ESTIMATED (proxy)",
        "Share of destructive_interlock blocks that look unneeded: within %d tool calls an equivalent command "
        "ran unblocked without the '# interlock-ok:' marker (same program, same targets, every flag kept), or "
        "the user's next prompt let it through (\"go ahead\", \"it's fine\", \"false positive\", ...). A safer "
        "rewrite (a flag dropped, a narrower target) is the interlock working and is not counted."
        % INTERLOCK_WINDOW,
        {"blocks": len(blocks), "equivalent_retry": equivalent, "user_override": user_ok,
         "rewritten_any": rewritten, "outcomes": _count(i["outcome"] for i in blocks)}))

    # correction detector precision
    fires = []
    for skey, s in sessions.items():
        v = summaries.get(str(s["main"])) if s["main"] else None
        for f in (v or {}).get("correction_fired") or []:
            if _in_window(f["ts"], cutoff_iso):
                fires.append(dict(f, session=Path(skey).name))
    smp = correction_sample(sample_path, fires)
    strict = sum(bool(f["strict"]) for f in fires)
    min_lab = READINESS_TARGETS["correction_precision"]["min_n"]
    if smp["labelled"] >= min_lab:
        val, n, label = smp["yes"] / smp["labelled"], smp["labelled"], "MEASURED (hand-labelled sample)"
    else:
        val, n, label = (strict / len(fires) if fires else None), len(fires), "ESTIMATED (stricter-matcher proxy)"
    rows.append(_row(
        "correction_precision", "Correction detector precision", val, n, label,
        "Share of correction_watch fires (typed main-session prompts its matcher flags) that really are "
        "corrections. With at least %d hand-labelled rows in the sample file, labelled yes / labelled; until "
        "then, the share of fires a stricter, high-precision phrase list also matches." % min_lab,
        {"fires": len(fires), "strict_agree": strict, "sample": smp},
        note="" if smp["labelled"] >= min_lab else "label the sample file to measure it: %s" % (smp["path"] or
                                                                                                 "(not written)")))

    # hook latency and errors (core hooks only; Claude Code records a run when it printed, failed or timed out)
    runs = [r for v in summaries.values() for r in v.get("hook_runs") or []
            if r[2] in hooks_core and _in_window(r[0], cutoff_iso)]
    ms = sorted(r[3] for r in runs if r[3] is not None)
    p95 = ms[int(0.95 * (len(ms) - 1))] if ms else None
    errs = [r for r in runs if r[4]]
    by_script: dict = {}
    for r in runs:
        a = by_script.setdefault(r[2], {"runs": 0, "errors": 0, "ms": []})
        a["runs"] += 1
        a["errors"] += int(bool(r[4]))
        if r[3] is not None:
            a["ms"].append(r[3])
    for a in by_script.values():
        xs = sorted(a.pop("ms"))
        a["p95_ms"] = xs[int(0.95 * (len(xs) - 1))] if xs else None
    hook_note = "" if runs else "no core hook runs recorded in the transcripts"
    rows.append(_row(
        "hook_latency_p95", "Hook latency (p95)", p95, len(ms), "MEASURED",
        "95th percentile durationMs of core hook runs that the transcripts record (hook attachments and Stop "
        "hook summaries). Claude Code records a run only when the hook printed, failed or timed out, so quiet "
        "runs are missing and this leans slow.", {"by_script": by_script}, note=hook_note))
    rows.append(_row(
        "hook_errors", "Hook errors", len(errs) / len(runs) if runs else None, len(runs), "MEASURED",
        "Share of recorded core hook runs that failed (non-blocking error), were cancelled or timed out. A "
        "deliberate block (exit 2) is not an error.",
        {"errors": len(errs), "kinds": _count(r[4] for r in errs)}, note=hook_note))

    # bench
    root = bench_dir or bench_root()
    f = latest_bench(root)
    data = _read_json(f, None) if f else None
    recs = data.get("records") if isinstance(data, dict) else None
    if isinstance(recs, list):
        cells = bench_cells(recs)
        arm = [c for c in cells if c["arm"] == BENCH_ARM and c["polarity"] == "positive"]
        k, n = sum(c["k"] for c in arm), sum(c["n"] for c in arm)
        rows.append(_row(
            "bench_applied", "Bench applied rate", k / n if n else None, n, "MEASURED",
            "In the newest bench round, the share of runs on the %s arm, positive scenarios, where the scenario "
            "scored applied; infrastructure errors left out of n. Every scenario/arm cell is in detail." % BENCH_ARM,
            {"results": str(f), "round": f.parent.name, "model": data.get("model"), "cells": cells,
             "families": {fam: {"k": sum(c["k"] for c in arm if c["family"] == fam),
                                "n": sum(c["n"] for c in arm if c["family"] == fam)}
                          for fam in sorted({c["family"] for c in arm})}}))
    else:
        rows.append(_row("bench_applied", "Bench applied rate", None, 0, "MEASURED",
                         "In the newest bench round, the share of %s-arm positive runs scored applied." % BENCH_ARM,
                         {"root": str(root)}, note="no bench results.json under the bench output folder"))

    # leak-gate catches, from shell calls in the transcripts
    gate = [g for v in summaries.values() for g in v.get("gates") or [] if _in_window(g["ts"], cutoff_iso)]
    caught = [g for g in gate if g["caught"]]
    rows.append(_row(
        "leak_gate_catches", "Leak-gate catches", len(caught) / len(gate) if gate else None, len(gate),
        "MEASURED",
        "Of the shell calls in the transcripts that ran a leak gate (leakcheck.py, provenance_check.py, gitleaks, "
        "or a git commit whose output shows the pre-commit gates), the share where a gate reported a finding. "
        "CI runs are not read. A high share means private detail keeps reaching commits.",
        {"runs": len(gate), "catches": len(caught),
         "by_gate": _count(x for g in caught for x in g["caught"])},
        note="" if gate else "no leak-gate runs found in the transcripts"))
    order = {"fail": 0, "warn": 1, "no-data": 2, "ok": 3}
    return {"label": "per row", "targets": READINESS_TARGETS, "rows": rows,
            "summary": _count(r["status"] for r in rows), "worst": min((r["status"] for r in rows),
                                                                       key=lambda x: order[x])}


# ---------------------------------------------------------------------------
# usage: tokens and an API-equivalent cost per day, project and session
# ---------------------------------------------------------------------------

# Claude API list prices in USD per million tokens: (input, output, cache write 5m, cache write 1h,
# cache read). Cache writes are 1.25x input for the 5-minute TTL and 2x for the 1-hour TTL; cache reads
# differ by model. First-party API rates as published (Anthropic pricing, checked 2026-10). A model not
# listed here gets cost null, never a guess. "long" is a higher tier for prompts above a token count.
MODEL_PRICES = {
    "claude-fable-5-1":  (10.00, 50.00, 12.50, 20.00, 0.25),
    "claude-mythos-5-1": (10.00, 50.00, 12.50, 20.00, 0.25),
    "claude-fable-5":    (10.00, 50.00, 12.50, 20.00, 1.00),
    "claude-mythos-5":   (10.00, 50.00, 12.50, 20.00, 1.00),
    "claude-opus-5-5":   (4.00, 20.00, 5.00, 8.00, 0.20),
    "claude-opus-5":     (5.00, 25.00, 6.25, 10.00, 0.50),
    "claude-opus-4-8":   (5.00, 25.00, 6.25, 10.00, 0.50),
    "claude-opus-4-7":   (5.00, 25.00, 6.25, 10.00, 0.50),
    "claude-opus-4-6":   (5.00, 25.00, 6.25, 10.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 4.00, 0.20),
    "claude-sonnet-5":   (2.00, 10.00, 2.50, 4.00, 0.20),
    "claude-sonnet-4-6": (3.00, 15.00, 3.75, 6.00, 0.30),
    "claude-haiku-5-5":  (0.10, 0.50, 0.125, 0.20, 0.01),
    "claude-haiku-4-5":  (1.00, 5.00, 1.25, 2.00, 0.10),
}
MODEL_PRICE_TIERS = {"claude-haiku-5-5": (100_000, (0.50, 2.50, 0.625, 1.00, 0.05))}   # prompt > 100k tokens
FAST_PRICE_X = {"claude-opus-5-5": 2.0, "claude-opus-5": 2.0}   # fast mode; other models' fast turns: null
USAGE_LABEL = "API-equivalent estimate"
TOP_USAGE_SESSIONS = 50


def normalise_model(model: str) -> str:
    m = (model or "").strip().lower()
    m = re.sub(r"\[.*?\]$", "", m)
    m = m.split("@")[0]
    for pre in ("anthropic.", "us.anthropic.", "eu.anthropic.", "global.anthropic."):
        if m.startswith(pre):
            m = m[len(pre):]
    return re.sub(r"-\d{8}$", "", m)


def turn_cost(model: str, inp: int, cw: int, cr: int, out: int, cw1h: int = 0, speed: str = ""):
    """API-equivalent USD for one assistant turn, or None for a model the price table does not list."""
    parts = turn_cost_parts(model, inp, cw, cr, out, cw1h, speed)
    return None if parts is None else parts[0] + parts[1]


def turn_cost_parts(model: str, inp: int, cw: int, cr: int, out: int, cw1h: int = 0, speed: str = ""):
    """(context carry, work) in API-equivalent USD for one turn, or None when unpriced. Carry is the cache
    read and cache write: what re-sending and storing the context costs. Work is the input and output."""
    m = normalise_model(model)
    pr = MODEL_PRICES.get(m)
    if pr is None:
        return None
    tier = MODEL_PRICE_TIERS.get(m)
    if tier and inp + cw + cr > tier[0]:
        pr = tier[1]
    x = 1.0
    if speed == "fast":
        if m not in FAST_PRICE_X:
            return None
        x = FAST_PRICE_X[m]
    cw1h = min(cw1h, cw)
    return (x * ((cw - cw1h) * pr[2] + cw1h * pr[3] + cr * pr[4]) / 1e6, x * (inp * pr[0] + out * pr[1]) / 1e6)


def _acc():
    return {"turns": 0, "input": 0, "output": 0, "cache_creation": 0, "cache_read": 0, "total": 0,
            "est_cost_usd": 0.0, "unpriced_turns": 0, "unpriced_tokens": 0}


def _add(a: dict, t: dict, cost):
    a["turns"] += 1
    for k in ("input", "output", "cache_creation", "cache_read", "total"):
        a[k] += t[k]
    if cost is None:
        a["unpriced_turns"] += 1
        a["unpriced_tokens"] += t["total"]
    else:
        a["est_cost_usd"] += cost


def _fin(a: dict) -> dict:
    a["est_cost_usd"] = round(a["est_cost_usd"], 4)
    return a


SUBSCRIPTION_HOWTO = ('to set usage against what you pay, write a JSON list such as [{"from": "2026-01", "plan": '
                      '"<plan name>", "monthly_usd": 100}] to %s (one entry per plan change; the core plugin\'s '
                      'subscription_file option or GNOMONKIT_SUBSCRIPTION_FILE points elsewhere)')


def subscription_file(gk: Path | None = None) -> Path:
    """The subscription schedule: the core plugin's subscription_file option, else ~/.gnomonkit/subscription.json."""
    raw = (os.environ.get("CLAUDE_PLUGIN_OPTION_SUBSCRIPTION_FILE") or os.environ.get("GNOMONKIT_SUBSCRIPTION_FILE")
           or "").strip()
    return Path(raw).expanduser() if raw else (gk or gnomonkit_home()) / "subscription.json"


def load_subscription(path: Path | None) -> dict:
    """{"status": ok|missing|invalid, "source", "schedule": [{from, plan, monthly_usd}] sorted by from, "note"}."""
    out = {"status": "missing", "source": str(path) if path else None, "schedule": [], "note": ""}
    if not path or not path.is_file():
        out["note"] = SUBSCRIPTION_HOWTO % (path or "~/.gnomonkit/subscription.json")
        return out
    raw = _read_json(path, None)
    rows = []
    if isinstance(raw, list):
        for r in raw:
            if not isinstance(r, dict):
                continue
            frm, usd = str(r.get("from") or ""), r.get("monthly_usd")
            if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", frm) and isinstance(usd, (int, float)) and usd >= 0 \
                    and not isinstance(usd, bool):
                rows.append({"from": frm, "plan": str(r.get("plan") or ""), "monthly_usd": float(usd)})
    if not rows:
        out.update(status="invalid", note="no usable entry in the subscription file; " + SUBSCRIPTION_HOWTO % path)
        return out
    out.update(status="ok", schedule=sorted(rows, key=lambda r: r["from"]))
    return out


def plan_for(month: str, schedule: list):
    """The entry in effect for a YYYY-MM month: the latest `from` at or before it. None before the first one."""
    best = None
    for r in schedule:
        if r["from"] <= month:
            best = r
    return best


def plan_share(by_month: dict, month_cost: dict, sub_window: dict) -> float:
    """Per month: (this row's priced cost / everyone's) x the plan dollars for that month's window days.
    The one allocation the usage and activities sections both use."""
    v = 0.0
    for mo, c in by_month.items():
        if month_cost.get(mo) and sub_window.get(mo) is not None:
            v += c / month_cost[mo] * sub_window[mo]
    return round(v, 4)


def _days_in_month(month: str) -> int:
    y, m = int(month[:4]), int(month[5:7])
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - dt.date(y, m, 1)).days


def usage(sessions: dict, summaries: dict, cutoff_iso: str, now: float | None = None,
          subscription: dict | None = None) -> dict:
    """Tokens per assistant turn (each API message id once, subagents with their parent session), by UTC day,
    project and session, with an API-equivalent cost and, given a subscription schedule, each one's share of
    what the plan cost over the same days."""
    now = now or time.time()
    sub = subscription or load_subscription(None)
    sched = sub["schedule"] if sub.get("status") == "ok" else []
    seen: set = set()
    daily: dict = {}
    projects: dict = {}
    proj_month: dict = defaultdict(lambda: defaultdict(float))     # project -> month -> priced cost
    per_session: list = []
    models: dict = {}
    total = _acc()
    for skey, s in sessions.items():
        files = ([s["main"]] if s["main"] else []) + s["subs"]
        main_v = summaries.get(str(s["main"])) if s["main"] else None
        sa = _acc()
        s_month: dict = defaultdict(float)
        first = last = ""
        for f in files:
            v = summaries.get(str(f))
            for row in (v or {}).get("turn_usage") or []:
                mid, ts, model, inp, cw, cr, out = row[:7]
                cw1h, speed = (row[7], row[8]) if len(row) > 8 else (0, "")
                if mid in seen:
                    continue
                ts = ts or (v.get("last_ts") or "")
                if not _in_window(ts, cutoff_iso):
                    continue
                seen.add(mid)
                t = {"input": inp, "output": out, "cache_creation": cw, "cache_read": cr,
                     "total": inp + out + cw + cr}
                if not t["total"]:
                    continue                                   # a synthetic or empty turn bills nothing
                cost = turn_cost(model, inp, cw, cr, out, cw1h, speed)
                day = ts[:10] if ts else "unknown"
                _add(daily.setdefault(day, _acc()), t, cost)
                _add(sa, t, cost)
                _add(total, t, cost)
                mk = normalise_model(model) or "unknown"
                _add(models.setdefault(mk, _acc()), t, cost)
                if cost is not None and ts:
                    s_month[ts[:7]] += cost
                first = min(first, ts) if first else ts
                last = max(last, ts)
        if not sa["turns"]:
            continue
        pk = s["project"]
        p = projects.setdefault(pk, dict(_acc(), project=pk, label=_project_label(main_v, pk), sessions=0))
        p["sessions"] += 1
        for k in ("turns", "input", "output", "cache_creation", "cache_read", "total", "unpriced_turns",
                  "unpriced_tokens"):
            p[k] += sa[k]
        p["est_cost_usd"] += sa["est_cost_usd"]
        for mo, c in s_month.items():
            proj_month[pk][mo] += c
        per_session.append(dict(_fin(sa), session=Path(skey).name, project=pk,
                                label=_project_label(main_v, pk), first_ts=first, last_ts=last,
                                title=(main_v or {}).get("title") or "", _months=dict(s_month)))
    for mk, a in models.items():
        if mk not in MODEL_PRICES:
            a["est_cost_usd"] = None
        else:
            _fin(a)

    # the window's calendar days (UTC), and per month: plan dollars for those days against the API-equivalent cost
    t0 = dt.datetime.fromisoformat(cutoff_iso[:19]).replace(tzinfo=dt.timezone.utc).timestamp()
    d1 = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
    window_days: dict = {}                              # month -> days of the window in it (fractional at the ends)
    y, m = int(cutoff_iso[:4]), int(cutoff_iso[5:7])
    while (y, m) <= (d1.year, d1.month):
        ms = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc).timestamp()
        me = dt.datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=dt.timezone.utc).timestamp()
        window_days["%04d-%02d" % (y, m)] = max(0.0, (min(now, me) - max(t0, ms)) / 86400)
        y, m = y + (m == 12), m % 12 + 1
    month_cost: dict = defaultdict(float)
    for day, a in daily.items():
        if day != "unknown":
            month_cost[day[:7]] += a["est_cost_usd"]
    months = []
    sub_window: dict = {}
    for mo in sorted(set(window_days) | set(month_cost)):
        plan = plan_for(mo, sched)
        dim = _days_in_month(mo)
        wd = window_days.get(mo, 0)
        sw = plan["monthly_usd"] * wd / dim if plan else None
        sub_window[mo] = sw
        months.append({"month": mo, "plan": plan["plan"] if plan else None,
                       "subscription_usd": plan["monthly_usd"] if plan else None,
                       "subscription_usd_window": round(sw, 4) if sw is not None else None,
                       "api_equiv_usd": round(month_cost.get(mo, 0.0), 4), "window_days": round(wd, 3),
                       "days_in_month": dim, "covers_full_month": abs(wd - dim) < 1e-6,
                       "value_multiple": round(month_cost.get(mo, 0.0) / sw, 2) if sw else None})

    def share(by_month: dict):
        return plan_share(by_month, month_cost, sub_window) if sched else None

    for day, a in daily.items():
        plan = plan_for(day[:7], sched) if day != "unknown" else None
        a["subscription_usd_share"] = round(plan["monthly_usd"] / _days_in_month(day[:7]), 4) if plan else None
    for pk, p in projects.items():
        p["subscription_usd_share"] = share(proj_month[pk])
    for r in per_session:
        r["subscription_usd_share"] = share(r.pop("_months"))
    period_sub = round(sum(v for v in sub_window.values() if v is not None), 4) if sched else None
    allocated = round(sum(sub_window[mo] for mo in sub_window if sub_window[mo] is not None and month_cost.get(mo)),
                      4) if sched else None
    total = _fin(total)
    total["subscription_usd"] = period_sub
    total["subscription_usd_allocated"] = allocated
    planned = sum(c for mo, c in month_cost.items() if sub_window.get(mo) is not None)   # months a plan covers
    total["api_equiv_usd_planned"] = round(planned, 4) if sched else None
    total["value_multiple"] = round(planned / period_sub, 2) if period_sub else None

    # month to date (UTC), in API-equivalent terms, against the plan
    cur = d1.strftime("%Y-%m")
    start = dt.datetime(d1.year, d1.month, 1, tzinfo=dt.timezone.utc).timestamp()
    elapsed = max((now - start) / 86400, 1 / 24)
    dim = _days_in_month(cur)
    spend = month_cost.get(cur, 0.0)
    plan = plan_for(cur, sched)
    daily_plan = plan["monthly_usd"] / dim if plan else None
    projected = spend / elapsed * dim
    mtd = {"month": cur, "plan": plan["plan"] if plan else None, "subscription_usd": plan["monthly_usd"] if plan else None,
           "api_equiv_usd": round(spend, 4), "days_elapsed": round(elapsed, 2), "days_in_month": dim,
           "projected_api_equiv_usd": round(projected, 2),
           "projected_value_multiple": round(projected / plan["monthly_usd"], 2) if plan and plan["monthly_usd"] else None,
           "plan_value_days_used": round(spend / daily_plan, 1) if daily_plan else None,
           "complete": cutoff_iso[:10] <= dt.date(d1.year, d1.month, 1).isoformat(),
           "label": "ESTIMATED (linear run rate)"}
    if not mtd["complete"]:
        mtd["note"] = "the window starts after the 1st, so month-to-date is short; run with a longer --since"
    return {
        "label": USAGE_LABEL, "note": "a subscription is not billed per token; this is what the same tokens "
                                      "would cost at Claude API list prices",
        "prices_usd_per_mtok": {k: dict(zip(("input", "output", "cache_write_5m", "cache_write_1h",
                                             "cache_read"), v)) for k, v in MODEL_PRICES.items()},
        "subscription": {"status": sub.get("status"), "source": sub.get("source"), "schedule": sched,
                         "note": sub.get("note") or "",
                         "method": "plan dollars per calendar day = monthly_usd / days in that month; a project's or "
                                   "session's share = its part of each month's API-equivalent cost x the plan dollars "
                                   "for that month's days in the window (unpriced turns carry no share)"},
        "total": total,
        "months": months,
        "month_to_date": mtd,
        "daily": [dict(_fin(a), date=d) for d, a in sorted(daily.items())],
        "daily_timezone": "UTC",                   # each row's date is a UTC calendar day, not a local one
        "projects": sorted((_fin(p) for p in projects.values()), key=lambda p: -p["est_cost_usd"]),
        "sessions": sorted(per_session, key=lambda r: -r["est_cost_usd"])[:TOP_USAGE_SESSIONS],
        "session_count": len(per_session),
        "models": dict(sorted(models.items(), key=lambda kv: -kv[1]["total"])),
        # for the activities section's plan share; collect() removes it before writing
        "_alloc": {"month_cost": dict(month_cost), "sub_window": dict(sub_window) if sched else {}},
    }


# ---------------------------------------------------------------------------
# activities: where the cost goes
# ---------------------------------------------------------------------------
ACTIVITY_BANDS = (200_000, 400_000, 600_000)
TOP_PROCESSES = 10
TOP_PROGRAMS = 10
PROCESS_MIN_SESSIONS = 3          # as automate-repeats: a pattern inside one session is a loop, not a process
PROCESS_N = (3, 5)
ACTIVITY_METHOD = (
    "Each assistant turn (one API message) is priced as in the usage section and split in two: context carry "
    "(cache read + cache write, the cost of re-sending and storing the context) and work (input + output). "
    "The turn's cost goes to the activity of the tool calls it made, split evenly when it made several; a turn "
    "with no tool call is reasoning/reply. Every turn of a subagent transcript goes to 'subagent: <type>', "
    "with the subagent's own activities as its breakdown. Share of plan uses the usage section's allocation. "
    "A process is a repeated command sequence as automate-repeats finds it; its cost is the attributed cost "
    "of the calls in each run.")


def _act_acc() -> dict:
    return {"cost": 0.0, "carry": 0.0, "work": 0.0, "turns": 0, "unpriced_turns": 0, "sessions": set(),
            "months": defaultdict(float), "programs": defaultdict(lambda: [0.0, 0]), "inner": {}}


def _act_add(a: dict, frac: float, carry: float, work: float, month: str, skey: str, priced: bool):
    a["turns"] += 1
    a["sessions"].add(skey)
    if not priced:
        a["unpriced_turns"] += 1
        return
    a["cost"] += (carry + work) * frac
    a["carry"] += carry * frac
    a["work"] += work * frac
    if month:
        a["months"][month] += (carry + work) * frac


def _act_row(name: str, a: dict, total: float, alloc: dict | None) -> dict:
    row = {"activity": name,
           "group": name.split(":")[0] if name.startswith(("subagent:", "mcp:", "other:")) else "core",
           "cost_usd": round(a["cost"], 4), "share": round(a["cost"] / total, 4) if total else None,
           "context_carry_usd": round(a["carry"], 4), "work_usd": round(a["work"], 4),
           "context_carry_share": round(a["carry"] / a["cost"], 4) if a["cost"] else None,
           "turns": a["turns"], "sessions": len(a["sessions"]), "unpriced_turns": a["unpriced_turns"],
           "subscription_usd_share": plan_share(a["months"], alloc["month_cost"], alloc["sub_window"])
           if alloc and alloc.get("sub_window") else None}
    if a["programs"]:
        row["programs"] = [{"program": p, "cost_usd": round(c, 4), "turns": n}
                           for p, (c, n) in sorted(a["programs"].items(), key=lambda kv: -kv[1][0])[:TOP_PROGRAMS]]
    if a["inner"]:
        row["inner"] = sorted((_act_row(k, v, a["cost"], alloc) for k, v in a["inner"].items()),
                              key=lambda r: -r["cost_usd"])
        for r in row["inner"]:
            r.pop("subscription_usd_share", None)          # the share is the subagent's, not split again
    return row


def _sub_type(f: Path, sm: dict, by_tid: dict) -> str:
    meta = _read_json(f.with_name(f.stem + ".meta.json"), {}) or {}
    if meta.get("agentType"):
        return str(meta["agentType"])[:60]
    pk = by_tid.get(meta.get("toolUseId") or "")
    return (sm[pk]["agent_calls"][meta["toolUseId"]].get("type") if pk else "") or "unknown"


def activities(sessions: dict, summaries: dict, cutoff_iso: str, alloc: dict | None = None) -> dict:
    """Attributed API-equivalent cost per activity, per repeated process and per context-size band (see
    ACTIVITY_METHOD). Turns are taken exactly as the usage section takes them, so the totals agree."""
    acts: dict = defaultdict(_act_acc)
    seen: set = set()
    total = {"cost": 0.0, "carry": 0.0, "work": 0.0, "turns": 0, "unpriced_turns": 0, "split_turns": 0}
    band_cost = {t: 0.0 for t in ACTIVITY_BANDS}
    band_turns = {t: 0 for t in ACTIVITY_BANDS}
    priced_turns = 0
    scans = []
    for skey, s in sessions.items():
        files = ([s["main"]] if s["main"] else []) + s["subs"]
        sm = {str(f): summaries[str(f)] for f in files if str(f) in summaries}
        by_tid = {tid: fk for fk, v in sm.items() for tid in v["agent_calls"]}
        for f in files:
            v = sm.get(str(f))
            if not v:
                continue
            sub = None if s["main"] and f == s["main"] else "subagent: " + _sub_type(f, sm, by_tid)
            call_mid = v.get("call_mid") or []
            by_mid: dict = defaultdict(list)
            for i, m in enumerate(call_mid):
                by_mid[m].append(i)
            call_cost = [0.0] * len(call_mid)
            counted: set = set()
            for row in v.get("turn_usage") or []:
                mid, ts, model, inp, cw, cr, out = row[:7]
                cw1h, speed = (row[7], row[8]) if len(row) > 8 else (0, "")
                if mid in seen:
                    continue
                ts = ts or (v.get("last_ts") or "")
                if not _in_window(ts, cutoff_iso):
                    continue
                seen.add(mid)
                if not inp + cw + cr + out:
                    continue
                parts = turn_cost_parts(model, inp, cw, cr, out, cw1h, speed)
                priced = parts is not None
                carry, work = parts or (0.0, 0.0)
                cost = carry + work
                month = ts[:7] if ts else ""
                calls = (v.get("turn_acts") or {}).get(mid) or [[REASONING, ""]]
                n = len(calls)
                grouped: dict = defaultdict(lambda: [0, defaultdict(int)])
                for act, detail in calls:
                    grouped[act][0] += 1
                    if detail:
                        grouped[act][1][detail] += 1
                total["turns"] += 1
                total["split_turns"] += int(len(grouped) > 1)
                if not priced:
                    total["unpriced_turns"] += 1
                else:
                    priced_turns += 1
                    total["cost"] += cost
                    total["carry"] += carry
                    total["work"] += work
                    for t in ACTIVITY_BANDS:
                        if inp + cw + cr > t:
                            band_cost[t] += cost
                            band_turns[t] += 1
                if sub:
                    _act_add(acts[sub], 1.0, carry, work, month, skey, priced)
                for act, (k, details) in grouped.items():
                    holder = acts[sub]["inner"] if sub else acts
                    a = holder.setdefault(act, _act_acc())
                    _act_add(a, k / n, carry, work, month, skey, priced)
                    for d, dk in details.items():
                        a["programs"][d][0] += cost * dk / n
                        a["programs"][d][1] += 1
                for i in by_mid.get(mid, ()):
                    call_cost[i] = cost / n
                    counted.add(i)
            steps = [st for st in v.get("proc_steps") or [] if st[1] in counted]
            if steps:
                scans.append((s["project"], str(f), sub is None, {"steps": steps, "calls": [0] * len(call_cost),
                                                                   "cost": call_cost}))
    tc = total["cost"]
    rows = sorted((_act_row(k, a, tc, alloc) for k, a in acts.items()), key=lambda r: (-r["cost_usd"], r["activity"]))
    procs = []
    if DR and scans:
        found = DR.find_sequences(scans, PROCESS_N[0], PROCESS_N[1], PROCESS_MIN_SESSIONS)
        found = DR.fold_variants(sorted(found, key=lambda r: (-r.get("cost_total", 0), -r["sessions"], r["pattern"])))
        for r in found[:TOP_PROCESSES]:
            procs.append({"pattern": r["pattern"], "shape": r["shape"], "runs": r["occurrences"],
                          "sessions": r["sessions"], "projects": r["projects"],
                          "cost_usd": round(r.get("cost_total", 0.0), 4),
                          "cost_usd_per_run": round(r.get("cost_each", 0.0), 4),
                          "share": round(r.get("cost_total", 0.0) / tc, 4) if tc else None,
                          "variants": len(r.get("variants") or [])})
    bands = [{"above": t, "cost_usd": round(band_cost[t], 4), "cost_share": round(band_cost[t] / tc, 4) if tc else None,
              "turns": band_turns[t], "turns_share": round(band_turns[t] / priced_turns, 4) if priced_turns else None}
             for t in ACTIVITY_BANDS]
    return {
        "label": USAGE_LABEL, "method": ACTIVITY_METHOD,
        "labels": {"cost_usd": "ESTIMATED (API-equivalent)", "turns": "MEASURED", "context_carry_usd": "ESTIMATED",
                   "processes": "MEASURED counts, ESTIMATED cost"},
        "total": {"cost_usd": round(tc, 4), "context_carry_usd": round(total["carry"], 4),
                  "work_usd": round(total["work"], 4),
                  "context_carry_share": round(total["carry"] / tc, 4) if tc else None,
                  "turns": total["turns"], "unpriced_turns": total["unpriced_turns"],
                  "split_turns": total["split_turns"]},
        "activities": rows,
        "processes": procs,
        "process_rule": {"min_sessions": PROCESS_MIN_SESSIONS, "min_steps": PROCESS_N[0], "max_steps": PROCESS_N[1],
                         "available": DR is not None},
        "context_bands": bands,
    }


def activities_table(a: dict) -> str:
    def c(x):
        return "-" if x is None else "$%.2f" % x

    def p(x):
        return "-" if x is None else "%d%%" % round(100 * x)
    t = a["total"]
    lines = ["Where it goes (%s; turns MEASURED)" % a["label"],
             "  total %s over %d turns; context carry %s of it; %d turns split across activities; unpriced %d" % (
                 c(t["cost_usd"]), t["turns"], p(t["context_carry_share"]), t["split_turns"], t["unpriced_turns"]),
             "", "  %-34s %10s %6s %8s %7s %7s %10s" % ("activity", "cost", "share", "carry", "turns", "sessions",
                                                      "plan share")]
    for r in a["activities"]:
        lines.append("  %-34s %10s %6s %8s %7d %7d %10s" % (
            r["activity"][:34], c(r["cost_usd"]), p(r["share"]), p(r["context_carry_share"]), r["turns"],
            r["sessions"], c(r["subscription_usd_share"])))
        for x in (r.get("inner") or [])[:5]:
            lines.append("    %-32s %10s %6s %8s" % (x["activity"][:32], c(x["cost_usd"]), p(x["share"]),
                                                    p(x["context_carry_share"])))
        if r.get("programs"):
            lines.append("    programs: " + ", ".join("%s %s" % (g["program"], c(g["cost_usd"]))
                                                     for g in r["programs"][:8]))
    lines += ["", "  context above   cost share   turns share"]
    for b in a["context_bands"]:
        lines.append("  %-13s %12s %13s" % (_n(b["above"]), p(b["cost_share"]), p(b["turns_share"])))
    lines += ["", "  processes (repeated in >= %d sessions)" % a["process_rule"]["min_sessions"]]
    if not a["processes"]:
        lines.append("    none" + ("" if a["process_rule"]["available"] else " (automate-repeats not found)"))
    for r in a["processes"]:
        lines.append("    %s total, %s per run, %d runs in %d sessions: %s" % (
            c(r["cost_usd"]), c(r["cost_usd_per_run"]), r["runs"], r["sessions"], r["pattern"][:120]))
    return "\n".join(lines)


def usage_table(u: dict, days: int | None = None) -> str:
    def c(x):
        return "-" if x is None else "$%.2f" % x
    t, sb, mtd = u["total"], u.get("subscription") or {}, u.get("month_to_date") or {}
    lines = ["Usage (%s; tokens MEASURED)" % u["label"],
             "  total %s tokens, %s; unpriced turns %d" % (_n(t["total"]), c(t["est_cost_usd"]), t["unpriced_turns"])]
    if sb.get("status") == "ok":
        lines.append("  plan dollars over the same days %s; API-equivalent value x%s" % (
            c(t.get("subscription_usd")), "-" if t.get("value_multiple") is None else t["value_multiple"]))
        lines.append("  month to date %s: %s API-equivalent, projected %s for the month vs plan %s (x%s); "
                     "%s days of plan value used" % (
                         mtd.get("month"), c(mtd.get("api_equiv_usd")), c(mtd.get("projected_api_equiv_usd")),
                         c(mtd.get("subscription_usd")), mtd.get("projected_value_multiple"),
                         mtd.get("plan_value_days_used")))
    else:
        lines.append("  month to date %s: %s API-equivalent, projected %s for the month" % (
            mtd.get("month"), c(mtd.get("api_equiv_usd")), c(mtd.get("projected_api_equiv_usd"))))
        lines.append("  no subscription schedule: " + (sb.get("note") or ""))
    lines += ["", "  %-10s %9s %9s %9s %9s %10s %10s" % ("day (UTC)", "input", "output", "cache wr", "cache rd", "cost",
                                                        "plan/day")]
    for d in (u["daily"][-days:] if days else u["daily"]):
        lines.append("  %-10s %9s %9s %9s %9s %10s %10s" % (d["date"], _n(d["input"]), _n(d["output"]),
                                                            _n(d["cache_creation"]), _n(d["cache_read"]),
                                                            c(d["est_cost_usd"]), c(d.get("subscription_usd_share"))))
    lines += ["", "  %-34s %8s %10s %10s %10s" % ("project", "sessions", "tokens", "cost", "plan share")]
    for p in u["projects"][:15]:
        lines.append("  %-34s %8d %10s %10s %10s" % (p["label"][:34], p["sessions"], _n(p["total"]),
                                                     c(p["est_cost_usd"]), c(p.get("subscription_usd_share"))))
    lines += ["", "  %-10s %-20s %-30s %8s %10s %10s" % ("session", "project", "title", "tokens", "cost", "plan share")]
    for r in u["sessions"][:10]:
        lines.append("  %-10s %-20s %-30s %8s %10s %10s" % (r["session"][:8], r["label"][:20], r["title"][:30],
                                                            _n(r["total"]), c(r["est_cost_usd"]),
                                                            c(r.get("subscription_usd_share"))))
    return "\n".join(lines)


def interjector_stats(root: Path, cutoff: float, verdicts_path: Path | None) -> dict:
    out = {"label": "MEASURED", "sessions": 0, "searches": 0, "guesses": 0, "answered": 0, "unanswered": 0,
           "right": 0, "wrong": 0, "unmarked": 0, "verdicts_source": None}
    if not root.is_dir():
        out["note"] = "no interjector store"
        return out
    verdicts = {}
    vp = verdicts_path or (root / "verdicts.json")
    raw = _read_json(vp, None) if vp else None
    if isinstance(raw, dict):
        raw = raw.get("guess-verdicts", raw)
        for k, v in raw.items():
            verdicts[str(k)] = (v.get("verdict") if isinstance(v, dict) else v) or ""
        out["verdicts_source"] = "file"

    def t_of(r):
        t = r.get("t")
        if isinstance(t, (int, float)):
            return float(t)
        try:
            return dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return time.time()

    for d in root.iterdir():
        p = d / "guesses.jsonl"
        if not p.is_file():
            continue
        out["sessions"] += 1
        guesses, replies = {}, set()
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or t_of(r) < cutoff:
                continue
            if r.get("kind") == "guess" and r.get("id"):
                guesses[str(r["id"])] = r
            elif r.get("kind") == "reply" and r.get("answers"):
                replies.add(str(r["answers"]))
            elif "kind" not in r:
                out["searches"] += 1
        for gid in guesses:
            out["guesses"] += 1
            if gid in replies:
                out["answered"] += 1
                v = verdicts.get(gid, "")
                if v in ("right", "wrong"):
                    out[v] += 1
                else:
                    out["unmarked"] += 1
            else:
                out["unanswered"] += 1
    if not out["verdicts_source"]:
        out["note"] = ("right/wrong are marked on the guess review page (python -m interjector guesses --html); "
                       "pass --verdicts with its copied block to count them")
    return out


def state_stats(home: Path, gk: Path, cutoff: float) -> dict:
    fires: dict = defaultdict(int)
    rdir = Path(os.environ.get("GNOMONKIT_ROUTER_STATE_DIR") or home / "skill-router")
    n = 0
    if rdir.is_dir():
        for f in rdir.glob("*.json"):
            d = _read_json(f, {}) or {}
            sk = d.get("skills") if isinstance(d, dict) else None
            if not isinstance(sk, dict):
                continue
            n += 1
            names = set()
            for k, t in sk.items():
                if isinstance(t, (int, float)) and t >= cutoff:
                    names.add(str(k).split(":", 1)[-1])
            for name in names:
                fires[name] += 1
    stale, checked = 0, 0
    fdir = gk / "freshness"
    if fdir.is_dir():
        for f in fdir.glob("*.json"):
            d = _read_json(f, {}) or {}
            checked += 1
            stale += len(d.get("stale") or []) if isinstance(d, dict) else 0
    return {"label": "MEASURED",
            "router_state": {"session_files": n, "sessions_per_skill": dict(sorted(fires.items(), key=lambda kv: -kv[1]))},
            "freshness_state": {"repos_checked": checked, "plugins_pending_now": stale},
            "history_state": history_stats(gk, cutoff)}


def _jsonl_since(path: Path, since_iso: str) -> list:
    out = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and str(d.get("ts") or "") >= since_iso:
                    out.append(d)
    except OSError:
        pass
    return out


def history_stats(gk: Path, cutoff: float) -> dict:
    """Core's local history and lessons, from their own logs (never the words searched): searches in
    the window, how many found something, how many were marked and how many marked a result as
    used; lessons injections and the distinct ids shown; the last index run."""
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff))
    hdir = gk / "history"
    queries = _jsonl_since(hdir / "queries.jsonl", since)
    marks = _jsonl_since(hdir / "marks.jsonl", since)
    last = {}
    for m in marks:                                   # the last mark of a search is the one that counts
        last[str(m.get("qid"))] = bool(m.get("used"))
    searches = len(queries)
    found = sum(1 for q in queries if (q.get("results") or 0) > 0)
    used = sum(1 for v in last.values() if v)
    run = _read_json(hdir / "state.json", {}) or {}
    inject = _jsonl_since(gk / "lessons" / "inject.jsonl", since)
    shown = {i for d in inject for i in (d.get("ids") or []) if isinstance(i, str)}
    return {"searches": searches, "found_something": found, "marked": len(last), "marked_used": used,
            "mark_rate": round(len(last) / searches, 3) if searches else None,
            "used_rate": round(used / len(last), 3) if last else None,
            "modes": dict(_count(str(q.get("mode") or "?") for q in queries)),
            "index_exists": (hdir / "history.db").is_file(),
            "last_run": {k: run.get(k) for k in ("started", "seconds", "records", "dropped", "excluded", "error")}
            if isinstance(run, dict) and run else None,
            "lessons": {"injections": len(inject), "distinct_ids": len(shown),
                        "by_event": dict(_count(str(d.get("event") or "?") for d in inject))}}


def collect(since_days: float = 30, home: Path | None = None, gk: Path | None = None, cache_path: Path | None = None,
            line_cap: int = LINE_CAP, verdicts: Path | None = None, now: float | None = None,
            projects: Path | None = None, digest: str | None = None, budget: int | None = None,
            sample_path: Path | None = None, bench_dir: Path | None = None,
            subscription_path: Path | None = None, triggers: list | None = None) -> dict:
    digest = digest_command() if digest is None else digest
    home = home or claude_home()
    follow = follow_patterns(trigger_files(home) if triggers is None else triggers)
    gk = gk or gnomonkit_home()
    now = now or time.time()
    cutoff = now - since_days * 86400
    cutoff_iso = dt.datetime.fromtimestamp(cutoff, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    sessions = discover(projects or home / "projects", cutoff)
    cache = Cache(cache_path)
    summaries: dict = {}
    for s in sessions.values():
        for f in ([s["main"]] if s["main"] else []) + s["subs"]:
            try:
                summaries[str(f)] = cache.get(f, line_cap, digest)
            except OSError:
                pass
    cache.save()

    rows, _ = subagent_rows(sessions, summaries, cutoff_iso)
    linked = [r for r in rows if r["linked"]]
    totals = {k: sum(r["spend"][k] for r in rows) for k in ("input", "cache_creation", "cache_read", "output", "total")}
    agg = {
        "spawns": len(rows), "linked": len(linked), "unlinked": len(rows) - len(linked),
        "unlinked_why": dict(sorted(_count(r.get("why_unlinked") for r in rows if not r["linked"]).items())),
        "background": sum(r["background"] for r in rows), "forks": sum(r["fork"] for r in rows),
        "nested": sum(1 for r in rows if (r["depth"] or 1) > 1),
        "worth_it": sum(1 for r in linked if r["worth_it"]), "not_worth_it": sum(1 for r in linked if not r["worth_it"]),
        "small": sum(1 for r in linked if r["small"]),
        "spend": totals, "kept_out": sum(r["kept_out"] for r in rows),
        "returned": sum(r["returned"] for r in linked), "net_kept_out": sum(r["net_kept_out"] for r in linked),
        "reads_avoided": sum(r["reads_avoided"] for r in linked),
        "notified_tokens": sum(r.get("notified_tokens") or 0 for r in linked),
        "labels": {"spawns": "MEASURED", "spend": "MEASURED", "parent_turns_after": "MEASURED",
                   "kept_out": "ESTIMATED", "returned": "ESTIMATED", "net_kept_out": "ESTIMATED",
                   "reads_avoided": "ESTIMATED", "worth_it": "ESTIMATED", "notified_tokens": "MEASURED"},
        "by_type": _group(rows, "type"), "by_model": _group(rows, "model"), "by_project": _group(rows, "project"),
        "by_week": _mark_weeks(dict(sorted(_group(rows, "week").items())), cutoff_iso, now),
        "not_worth_it_spawns": sorted([r for r in linked if not r["worth_it"]],
                                      key=lambda r: r["reads_avoided"] - r["spend"]["total"])[:40],
        "spawn_rows": sorted(rows, key=lambda r: r["ts"] or ""),
    }
    by_type = agg["by_type"]
    cheap = sum((by_type.get(t) or {}).get("spawns", 0) for t in CHEAP_TYPES)
    agg["cheap_types"] = {"types": {t: by_type.get(t) for t in CHEAP_TYPES}, "spawns": cheap,
                          "share_of_spawns": round(cheap / len(rows), 3) if rows else None}
    agg["labels"].update({sg: "SIGNAL" for sg in SIGNALS})

    digests = [d for v in summaries.values() for d in v["digests"] if _in_window(d["ts"], cutoff_iso)]
    known = [d for d in digests if d["full"] is not None]
    saved_chars = sum(max(0, d["full"] - d["printed"]) for d in known)
    digest_name = digest
    digest = {
        "command": digest_name or None,
        "calls": len(digests), "known": len(known), "unknown": len(digests) - len(known),
        "by_source": dict(_count(d["source"] for d in digests)),
        "printed_chars": sum(d["printed"] for d in digests), "full_chars": sum(d["full"] for d in known),
        "saved_chars": saved_chars, "saved_tokens": est(saved_chars),
        "reads_avoided": sum(est(max(0, d["full"] - d["printed"])) * d["turns_after"] for d in known),
        "labels": {"calls": "MEASURED", "printed_chars": "MEASURED", "full_chars": "MEASURED",
                   "saved_chars": "MEASURED", "saved_tokens": "ESTIMATED", "reads_avoided": "ESTIMATED"},
    }

    router: dict = {}
    for v in summaries.values():
        for r in v["router"]:
            if not _in_window(r["ts"], cutoff_iso):
                continue
            a = router.setdefault(r["skill"], {"suggested": 0, "taken": 0, "already_loaded": 0, "scratch_paths": 0,
                                               "followed": 0, "by": {}})
            a["suggested"] += 1
            a["taken"] += int(r["taken"])
            a["already_loaded"] += int(r["prior"] and not r["taken"])
            a["scratch_paths"] += int(r["scratch"])
            pats = follow.get(r["skill"])
            a["followed"] += int(bool(pats) and any(p.search(c) for p in pats for c in r.get("after") or []))
            by = r.get("by") or "unknown"
            a["by"][by] = a["by"].get(by, 0) + 1
    for name, a in router.items():
        a["open"] = a["suggested"] - a["already_loaded"]
        a["uptake"] = round(a["taken"] / a["open"], 3) if a["open"] else None
        a["follow_through"] = round(a["followed"] / a["suggested"], 3) if follow.get(name) and a["suggested"] \
            else None
    skills_self: dict = {}
    for v in summaries.values():
        for c in v.get("skill_self") or []:
            if _in_window(c["ts"], cutoff_iso):
                skills_self[c["skill"]] = skills_self.get(c["skill"], 0) + 1
    lock = _count(i["outcome"] for v in summaries.values() for i in v["interlock"] if _in_window(i["ts"], cutoff_iso))
    pal = {"sessions": 0, "injected": 0, "cited": 0, "by_channel": {}}
    for v in summaries.values():
        p = v["palace"]
        if not p["injected"] or not _in_window(p.get("ts") or "", cutoff_iso):
            continue
        pal["sessions"] += 1
        pal["injected"] += p["injected"]
        pal["cited"] += p["cited"]
        for ch, (n, c) in p["by_channel"].items():
            x = pal["by_channel"].setdefault(ch, [0, 0])
            x[0] += n
            x[1] += c
    pal["cite_rate"] = round(pal["cited"] / pal["injected"], 3) if pal["injected"] else None
    hooks = {
        "label": "MEASURED",
        "router": dict(sorted(router.items(), key=lambda kv: -kv[1]["suggested"])),
        "skills_self": dict(sorted(skills_self.items(), key=lambda kv: -kv[1])),
        "interlock": {"blocks": sum(lock.values()), "outcomes": dict(lock)},
        "correction_watch": {"fires": sum(1 for v in summaries.values() for t in v["correction"]
                                          if _in_window(t, cutoff_iso))},
        "freshness": {"notices": sum(1 for v in summaries.values() for t in v["freshness"]
                                     if _in_window(t, cutoff_iso))},
        "palace": pal,
    }

    saved_sub = agg["reads_avoided"]
    ctx = main_context(sessions, summaries, cutoff_iso, budget or context_budget())
    comp = compaction_stats(sessions, summaries, cutoff_iso)
    gen_at = dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds")
    use = usage(sessions, summaries, cutoff_iso, now, load_subscription(subscription_path or subscription_file(gk)))
    alloc = use.pop("_alloc", None)
    out = {
        "schema": 1, "generator": "gnomonkit core effectiveness", "version": VERSION,
        "generated_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds"),
        "since_days": since_days, "window_from": cutoff_iso,
        "sources": {"sessions": len(sessions), "transcripts": len(summaries), "cache_hits": cache.hits,
                    "scanned": cache.misses, "long_lines_skipped": sum(v["long_lines"] for v in summaries.values()),
                    "session_spend_total": sum(sum(v["usage"]) for v in summaries.values())},
        "headline": {
            "tokens_saved": {"value": saved_sub + digest["reads_avoided"], "label": "ESTIMATED",
                             "parts": {"subagents": saved_sub, "digest": digest["reads_avoided"]}},
            "subagent_spend": {"value": totals["total"], "label": "MEASURED"},
            "spawns": {"value": len(rows), "label": "MEASURED"},
            "worth_it": {"value": agg["worth_it"], "of": len(linked), "label": "ESTIMATED"},
        },
        "subagents": agg, "dispatched": dispatched_sessions(gk, sessions, summaries, cutoff_iso),
        "main_context": ctx, "digest": digest, "hooks": hooks,
        "compaction": comp, "budgets": budgets(comp, ctx["budget"], gen_at, since_days),
        "readiness": readiness(sessions, summaries, cutoff_iso, sample_path, bench_dir),
        "usage": use,
        "activities": activities(sessions, summaries, cutoff_iso, alloc),
        "interjector": interjector_stats(gk / "interjector", cutoff, verdicts),
        "state": state_stats(home, gk, cutoff),
        "method": {"chars_per_token": CHARS_PER_TOKEN, "uptake_window": UPTAKE_WINDOW,
                   "interlock_window": INTERLOCK_WINDOW, "small_tool_calls": SMALL_TOOL_CALLS,
                   "small_kept_tokens": SMALL_KEPT_TOKENS, "line_cap": line_cap,
                   "context_thresholds": list(CONTEXT_THRESHOLDS), "context_budget": ctx["budget"],
                   "redo_calls": REDO_CALLS, "reject_turns": REJECT_TURNS, "corrected_prompts": CORRECTED_PROMPTS,
                   "signal_misses": SIGNAL_MISSES},
    }
    return out


def _count(it) -> dict:
    c: dict = defaultdict(int)
    for x in it:
        c[x if x is not None else "unknown"] += 1
    return dict(c)


def _n(x) -> str:
    if x is None:
        return "-"
    a = abs(x)
    for div, suf in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if a >= div:
            return "%.1f%s" % (x / div, suf)
    return str(x)


def _sig(r: dict) -> str:
    if not r["n"]:
        return "-"
    return "%d/%d%s" % (r["hits"], r["n"], "~" if r["thin"] else "")


def table(m: dict) -> str:
    h, s, d = m["headline"], m["subagents"], m["digest"]
    lines = ["Effectiveness, last %g days (%d transcripts)" % (m["since_days"], m["sources"]["transcripts"]),
             "  tokens saved     %8s  ESTIMATED (subagents %s, digest %s)" % (
                 _n(h["tokens_saved"]["value"]), _n(h["tokens_saved"]["parts"]["subagents"]),
                 _n(h["tokens_saved"]["parts"]["digest"])),
             "  subagent spend   %8s  MEASURED" % _n(h["subagent_spend"]["value"]),
             "  spawns %d (linked %d), worth it %d, not %d, small %d" % (
                 s["spawns"], s["linked"], s["worth_it"], s["not_worth_it"], s["small"]),
             "", "  %-22s %6s %6s %10s %10s" % ("type", "spawns", "worth", "avoided", "spend")]
    for k, a in list(s["by_type"].items())[:10]:
        share = "-" if a["worth_it_share"] is None else "%d%%" % round(100 * a["worth_it_share"])
        lines.append("  %-22s %6d %6s %10s %10s" % (k[:22], a["spawns"], share, _n(a["reads_avoided"]), _n(a["spend"])))
    lines += ["", "  SPAWN SIGNALS (not ground truth; hits/n, ~ thin sample, - not measurable)",
              "  %-22s %6s %12s %12s %12s" % ("model / type", "spawns", "redone", "corrected", "rejected")]
    for label, groups in (("model", s.get("by_model") or {}), ("type", s["by_type"])):
        for k, a in list(groups.items())[:8]:
            lines.append("  %-22s %6d %12s %12s %12s" % ((label + ":" + k)[:22], a["spawns"],
                                                       *[_sig(a["signals"][sg]) for sg in SIGNALS]))
    ch = s.get("cheap_types") or {}
    if ch.get("spawns"):
        lines.append("  cheap types (%s): %d spawns, %d%% of all" % (", ".join(CHEAP_TYPES), ch["spawns"],
                                                                  round(100 * ch["share_of_spawns"])))
    else:
        lines.append("  cheap types (%s): none used in the window" % ", ".join(CHEAP_TYPES))
    dp = m.get("dispatched") or {}
    if dp.get("sessions"):
        t = dp["total"]
        lines += ["", "  DISPATCHED SESSIONS  %d (with a PR %d, merged %d); corrections per prompt %s  SIGNAL; "
                  "CI before merge: not recorded" % (dp["sessions"], t["with_pr"], t["merged"],
                                                     _sig(t["corrections_per_prompt"]))]
        for k, a in dp["by_model"].items():
            lines.append("  %-22s %6d sessions, PRs merged %s, corrections/prompt %s" % (
                "model:" + k[:15], a["sessions"], _sig(a["pr_merged"]), _sig(a["corrections_per_prompt"])))
    c = m.get("main_context") or {}
    if c.get("turns"):
        lines += ["", "  MAIN CONTEXT  %d sessions, %d turns, peak %s, median %s  MEASURED" % (
            c["sessions"], c["turns"], _n(c["peak"]), _n(c["median"])),
            "  re-read %s MEASURED; capped at budget %s it would be %s ESTIMATED" % (
                _n(c["reread"]), _n(c["budget"]), _n(c["reread_capped"])),
            "  compacted %d of %d sessions MEASURED" % (c["compacted_sessions"], c["sessions"]),
            "  %-12s %8s %8s" % ("over", "turns", "re-read")]
        for t, o in c["over"].items():
            lines.append("  %-12s %7d%% %7d%%" % (_n(int(t)), round(100 * o["turns_share"]),
                                                 round(100 * o["reread_share"])))
        lines.append("  %-36s %6s %8s %8s" % ("top sessions by re-read", "turns", "peak", "re-read"))
        for r in c["per_session"][:TOP_SESSIONS]:
            lines.append("  %-36s %6d %8s %8s" % ((r["project"] + " " + r["session"][:8])[:36], r["turns"],
                                                 _n(r["peak"]), _n(r["reread"])))
    else:
        lines += ["", "  MAIN CONTEXT  no main-session turns with usage in the window"]
    if d["command"]:
        lines += ["", "  %s calls %d (size known %d), saved %s tokens ESTIMATED" % (
            d["command"], d["calls"], d["known"], _n(d["saved_tokens"]))]
    else:
        lines += ["", "  output digest: not tracked (set --digest-command or GNOMONKIT_DIGEST_COMMAND)"]
    b = m.get("budgets") or {}
    if b.get("projects"):
        lines += ["", "  BUDGETS  %-28s %7s %7s %6s  %s" % ("project", "set", "suggest", "conf", "reason")]
        for r in sorted(b["projects"], key=lambda r: -r["sessions"])[:TOP_SESSIONS * 2]:
            lines.append("  %-37s %7s %7s %6s  %s" % (r["project"][:37], _n(r["current"]), _n(r["recommended"]),
                                                     r["confidence"], r["reason"][:70]))
    hk = m["hooks"]
    sug = sum(a["suggested"] for a in hk["router"].values())
    took = sum(a["taken"] for a in hk["router"].values())
    opn = sum(a["open"] for a in hk["router"].values())
    lines.append("  router suggestions %d (%d not already loaded), taken up %d; skill calls with no suggestion %d"
                 % (sug, opn, took, sum(hk.get("skills_self", {}).values())))
    lines.append("  interlock blocks %d; correction fires %d" % (
        hk["interlock"]["blocks"], hk["correction_watch"]["fires"]))
    rd = m.get("readiness") or {}
    if rd.get("rows"):
        lines += ["", "  READINESS  %-8s %-30s %8s %8s %6s" % ("status", "metric", "value", "target", "n")]
        for r in rd["rows"]:
            lines.append("  %-19s %-30s %8s %8s %6d" % (r["status"], r["metric"][:30], r["display"], r["target"],
                                                       r["n"]))
    ij = m["interjector"]
    lines.append("  interjector guesses %d (answered %d, right %d, wrong %d)" % (
        ij["guesses"], ij["answered"], ij["right"], ij["wrong"]))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", help="write the metrics file here")
    ap.add_argument("--since", type=float, default=30, help="days to look back (default 30)")
    ap.add_argument("--line-cap", type=int, default=LINE_CAP, help="skip parsing lines longer than this")
    ap.add_argument("--cache", help="cache file (default ~/.gnomonkit/effectiveness/cache.json)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--verdicts", help="interjector guess verdicts (the block the review page copies)")
    ap.add_argument("--projects", help="transcripts folder (default $CLAUDE_CONFIG_DIR/projects)")
    ap.add_argument("--digest-command", help="the output-digest wrapper to measure (default: the core plugin's "
                    "digest_command option or GNOMONKIT_DIGEST_COMMAND; none when unset)")
    ap.add_argument("--context-budget", type=int, help="main-session context budget in tokens (default: the core "
                    "plugin's context_budget option, GNOMONKIT_CONTEXT_BUDGET, else %d)" % DEFAULT_CONTEXT_BUDGET)
    ap.add_argument("--quiet", action="store_true", help="no table")
    ap.add_argument("--recommend", action="store_true", help="print the per-project context budget "
                    "recommendations as JSON instead of the table (writes nothing to any project)")
    ap.add_argument("--usage", action="store_true", help="print token usage and an API-equivalent cost by day, "
                    "project and session instead of the table")
    ap.add_argument("--days", type=int, help="with --usage: show only the last N days in the daily table")
    ap.add_argument("--activities", action="store_true", help="print where the API-equivalent cost goes: per "
                    "activity (context carry vs work), per repeated process, and per context-size band")
    ap.add_argument("--correction-sample", help="hand-labelling sample of correction-watch fires (default "
                    "~/.gnomonkit/effectiveness/correction-sample.jsonl)")
    ap.add_argument("--subscription-file", help="subscription schedule JSON (default: the core plugin's "
                    "subscription_file option, GNOMONKIT_SUBSCRIPTION_FILE, else ~/.gnomonkit/subscription.json)")
    ap.add_argument("--bench-dir", help="bench output folder holding <round>/results.json (default: bench's own "
                    "default, or GNOMONKIT_BENCH_DIR)")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    cache = None if a.no_cache else Path(a.cache) if a.cache else gnomonkit_home() / "effectiveness" / "cache.json"
    m = collect(a.since, cache_path=cache, line_cap=a.line_cap, verdicts=Path(a.verdicts) if a.verdicts else None,
                projects=Path(a.projects) if a.projects else None, digest=a.digest_command,
                budget=a.context_budget,
                sample_path=Path(a.correction_sample) if a.correction_sample
                else gnomonkit_home() / "effectiveness" / "correction-sample.jsonl",
                bench_dir=Path(a.bench_dir) if a.bench_dir else None,
                subscription_path=Path(a.subscription_file) if a.subscription_file else None)
    if a.json:
        write_private(Path(a.json), json.dumps(m, indent=1, ensure_ascii=False))
    if a.recommend:
        print(json.dumps(m["budgets"], indent=1, ensure_ascii=False))
    elif a.usage:
        print(usage_table(m["usage"], a.days))
    elif a.activities:
        print(activities_table(m["activities"]))
    elif not a.quiet:
        print(table(m))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
