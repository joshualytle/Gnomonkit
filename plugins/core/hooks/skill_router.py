#!/usr/bin/env python3
"""Surface the skill that applies, before and after the work. Never block.

WHY THIS EXISTS. In a large install most skills are never invoked at all, and
the un-fired ones are mostly discipline skills whose whole value is firing
unprompted -- a rule you have to remember to consult is a rule
that fires when you least need it.

WHY IT IS NOT A SEARCH. Semantic routing over skill descriptions is unreliable:
the right skill often does not rank near the top for a real intent, even when
every skill is reranked exhaustively. See triggers.yaml for the full reasoning. Matching a
command or a path does not need to understand the request, which is why it
works where embedding the description did not.

TWO RULES THAT KEEP IT FROM BECOMING NOISE.

  It NEVER blocks. Exit 0, always, with the suggestion as additionalContext.
  The destructive interlock blocks and must stay the only thing that does --
  a second blocking hook dilutes the first, and the interlock's entire value
  is that it is never dismissed as boilerplate.

  It fires ONCE per skill PER PHASE per episode. A rule matching every
  edit under content/ would fire forty times in an afternoon and be filtered
  out by the reader after the second. The state file is keyed on session id,
  so a new session starts fresh and a long one is not nagged.

WHY BOTH PHASES. A suggestion can be relevant, delivered, and still never acted
on, because of when it lands. PreToolUse lands after the model has COMPOSED the action:
the file content is already written, the commit already staged. The advice
these skills carry is about what to do before choosing that action, so it
arrives with nothing left to change, and taking it means discarding work in
flight. PostToolUse gives the same skill a second hearing at a point where
adjusting the NEXT step is cheap. Suppression is therefore keyed on
PHASE:SKILL -- a skill-only key would let the first phase silence the second,
and the second hook would run, match, and emit nothing.

FAILURE IS SILENT, BY DESIGN. Any exception exits 0 with nothing printed. A
suggestion hook that breaks a tool call has done far more damage than the
suggestion was ever worth.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "triggers.yaml")
# GNOMONKIT_ROUTER_STATE_DIR keeps a sandboxed run (e.g. Gnomonkit Bench) from writing to the home dir.
STATE_DIR = os.environ.get("GNOMONKIT_ROUTER_STATE_DIR") or os.path.join(
    os.path.expanduser("~"), ".claude", "skill-router")


def _plugin_name() -> str:
    """The owning plugin's name, so each plugin that ships this router keeps its own state file.

    A private overlay runs a copy of this router beside core's, and both PreToolUse hooks fire at
    once: sharing one file per session would let one read-modify-write drop the other's record.
    Read from the manifest, not the folder: installed plugins sit in a folder named after the version.
    """
    try:
        with open(os.path.join(HERE, "..", ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            return re.sub(r"[^A-Za-z0-9_-]", "", json.load(fh).get("name", "")) or "router"
    except (OSError, ValueError):
        return "router"


def load_rules(path: str = RULES) -> list:
    """The rules from triggers.yaml, or from its generated twin triggers.json.

    The hook runs under whatever Python the machine has. Where that
    interpreter has no pyyaml (a stock Windows Python, for one), a YAML-only
    loader returns [] and the router is a silent no-op -- exit 0, nothing
    printed, nothing to notice. The JSON twin is written from the YAML by
    build_triggers_json.py and checked for staleness by test_skill_router.py,
    so either source yields the same list. A YAML that is present but broken
    still returns [] rather than falling back: the twin would be stale too.
    """
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        try:
            with open(path, encoding="utf-8") as fh:
                return (yaml.safe_load(fh) or {}).get("triggers", []) or []
        except (OSError, Exception):  # noqa: B014 - yaml errors vary by version
            return []
    try:
        twin = os.path.splitext(path)[0] + ".json"
        with open(twin, encoding="utf-8") as fh:
            return (json.load(fh) or {}).get("triggers", []) or []
    except (OSError, ValueError):
        return []


def _relations(path: str = None, hub_limit: int = 10) -> dict:
    """skill -> complementary skills, with generator habits pruned out.

    The pruning is inlined rather than imported from build_relations, which
    pulls in the whole benchmark module: this runs on every matching tool call
    and must stay a file read.

    A link named by more than `hub_limit` skills is a habit, not a
    relationship. Generated links tend to pile onto a few general skills (a
    planning skill, a delegation skill) that end up linked from a large share
    of the library. A pointer that applies to half the library tells the reader nothing and
    crowds out the specific ones.
    """
    p = path or os.path.join(HERE, "relations.json")
    try:
        with open(p, encoding="utf-8") as fh:
            rel = json.load(fh)
    except (OSError, ValueError):
        return {}
    freq = {}
    for v in rel.values():
        for x in (v or []):
            freq[x] = freq.get(x, 0) + 1
    hubs = {k for k, n in freq.items() if n > hub_limit}
    return {k: [x for x in (v or []) if x not in hubs] for k, v in rel.items()}


def _repo_of(cwd: str) -> str:
    return os.path.basename((cwd or "").rstrip("\\/")).lower()


# A command QUOTED inside echo/printf/grep is being displayed, not run. The
# destructive interlock learned this first; a router that suggests diff-review
# because someone echoed the words "git commit" is noise of the worst kind,
# since it is obviously wrong to the reader and teaches them to stop looking.
_DISPLAYED = re.compile(r"(?:^|[|;&]\s*)\s*(?:echo|printf|grep|rg|cat|less)\b")

# The agent's OWN scratchpad is not the user's project. A Write or a cd into
# AppData/Local/Temp/claude/<session>/ is a throwaway probe, and suggesting
# silent-no-op or skill-creator for work that will not survive the hour is
# a common source of false firings. Matched against the path OR the command,
# because the noise arrives both ways: a Write to a temp file, and a shell
# variable assigned one.
_SCAFFOLD = re.compile(
    r"(?:[/\\]|^)(?:AppData[/\\]Local[/\\])?Temp[/\\]claude[/\\]"
    r"|[/\\]scratchpad[/\\]", re.I)


def match(rules: list, tool: str, command: str, path: str, cwd: str) -> list:
    """Every rule whose tool, scope and pattern all match.

    Paths are compared with forward AND backslash tolerated in the pattern
    rather than normalised here, because a rule may legitimately want to
    anchor on a Windows-shaped path. `re.search` with IGNORECASE throughout:
    a trigger that misses `Dockerfile.prod` because it expected lowercase is
    a trigger that silently does nothing.
    """
    out = []
    # A rule may opt in with `scratchpad: true` when the scratchpad is where its
    # work really happens: a report page is drafted there before it is published.
    in_scratch = bool(_SCAFFOLD.search(path) or _SCAFFOLD.search(command))
    repo = _repo_of(cwd)
    for r in rules:
        if in_scratch and not r.get("scratchpad"):
            continue
        tools = r.get("tools") or []
        if tools and tool not in tools:
            continue
        scope = [s.lower() for s in (r.get("repos") or [])]
        if scope and repo not in scope:
            continue

        hit = False
        if command and not _DISPLAYED.search(command):
            for pat in r.get("command_patterns") or []:
                if re.search(pat, command, re.I):
                    hit = True
                    break
        if not hit and path:
            for pat in r.get("path_patterns") or []:
                if re.search(pat, path, re.I):
                    hit = True
                    break
        # Exclusions run AFTER the match, so a rule can claim a broad
        # directory and then hand specific files to a better-fitting rule --
        # content/privacy.md is legal text, not marketing copy, and two
        # suggestions on one edit is how this becomes wallpaper.
        if hit:
            subject = command or path
            if any(re.search(x, subject, re.I)
                   for x in (r.get("exclude_patterns") or [])):
                continue
            out.append(r)
    return out


# A sitting ends when the machine goes quiet for this long. Too short a gap
# (say 15 minutes) fragments continuous work; too long (say two hours) merges
# genuinely separate sittings. 45 minutes is a middle value; to tune it, replay
# your own tool-call history and look for the knee.
EPISODE_GAP_MINUTES = 45


def _already_fired(session: str, skill: str, phase: str = "pre") -> bool:
    """One suggestion per skill per EPISODE, recorded on disk.

    NOT ONCE PER SESSION. A session id survives resumes, and one long-lived
    session can span weeks and dozens of separate sittings, so "once per
    session" can mean once per month: after the first hour a suggestion is
    never offered again, which is not the same as being declined.

    An episode boundary is an idle gap, which recovers the unit a person would
    call "a sitting". Scoped per episode, a suggestion comes back a couple of
    times per sitting at most -- a prompt rather than a nag.

    SUB-DIVIDING FURTHER IS NOT WORTH IT. Re-arming per work-area inside an
    episode adds firings and bursts without reaching anyone new: episodes
    already capture the context switches, because people change what they
    are doing between sittings.

    Still a file per session so concurrent sessions cannot lose each other's
    writes; the records now age out by gap instead of living forever.
    """
    if not session:
        return False
    os.makedirs(STATE_DIR, exist_ok=True)
    p = os.path.join(STATE_DIR, f"{re.sub(r'[^A-Za-z0-9_-]', '', session)}-{_plugin_name()}.json")

    now = time.time()
    seen: dict = {}
    last = 0.0
    try:
        with open(p, encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict) and "skills" in raw:
            seen = {k: float(v) for k, v in (raw.get("skills") or {}).items()
                    if isinstance(v, (int, float))}
            last = float(raw.get("last") or 0.0)
        # Anything else is an OLD format: a bare list, or the first dict shape
        # that had no heartbeat. Neither can be aged safely, so treat it as a
        # previous episode rather than inherit a suppression of unknown age.
    except (OSError, ValueError, TypeError):
        seen, last = {}, 0.0

    # THE GAP IS MEASURED FROM THE LAST TIME THE HOOK RAN, not from the last
    # time something fired. Those differ, and the difference is a bug the tests
    # caught: a suppressed call writes no timestamp, so during a long sitting
    # where everything is already suppressed the newest fire-time goes stale
    # and the episode rolls over mid-flow -- re-suggesting in the middle of
    # exactly the continuous work the suppression exists to protect.
    if last and (now - last) > EPISODE_GAP_MINUTES * 60:
        seen = {}          # the gap ended the sitting: everything is fair again

    # KEYED ON PHASE:SKILL, not skill. Both hooks run for the same tool
    # call, and a single key would let PreToolUse silence PostToolUse
    # every time -- the second hook would be registered, run, match, and
    # emit nothing, which is worse than not adding it at all.
    key = f"{phase}:{skill}"
    fired = key in seen
    if not fired:
        seen[key] = now

    # Written on EVERY call, suppressed or not, because the heartbeat is the
    # whole point of the field.
    try:
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"last": now, "skills": seen}, fh)
    except OSError:
        pass
    return fired


# WHAT EACH PHASE CAN STILL ASK FOR. A generic "Consider invoking it" is
# easy to read and decline, even when it arrives every time.
#
# It is dismissible because it asks for nothing specific, and at PreToolUse it
# arrives once the action is already composed: the file content is written, the
# commit is staged. So each phase now asks for the one adjustment still
# available to it, which is also why both phases are worth running.
#
# IT NAMES THE LOAD. "Evaluate against this skill" invites an answer from the
# skill's name alone, without the skill ever being opened.
# The model judges a skill by the one line it can see, and in a large install
# the skill listing drops descriptions, so the name is often all it has. The
# ask is now the concrete call, with the plugin prefix the Skill tool needs.
PHRASING = {
    "pre": ("Before continuing, load this skill with the Skill tool (skip it if "
            "already loaded this session), check what you are ABOUT TO DO "
            "against it, and adjust if it applies:"),
    "post": ("Load this skill with the Skill tool (skip it if already loaded "
             "this session), check what you JUST DID against it, and correct "
             "it now if it applies:"),
}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    # PostToolUse sends the same tool_name and tool_input, plus the result.
    # Anything unrecognised is treated as "pre": the wording is then merely
    # less apt, where guessing "post" would tell the model to evaluate work it
    # has not done yet.
    event = str(payload.get("hook_event_name") or "PreToolUse")
    phase = "post" if event == "PostToolUse" else "pre"

    tool = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    command = str(ti.get("command") or "")
    path = str(ti.get("file_path") or ti.get("path") or "")
    cwd = payload.get("cwd") or ""
    session = payload.get("session_id") or ""

    if not command and not path:
        return 0

    hits = match(load_rules(), tool, command, path, cwd)
    fresh = [r for r in hits
             if not _already_fired(session, r["skill"], phase)]
    if not fresh:
        return 0

    # RELATED SKILLS RIDE ON THE TRIGGER, never on a search. Routing to a skill
    # from a free-text request is unreliable; this path is a deterministic path or
    # command match, so the anchor is already correct and the links only have
    # to be relevant to it. Attaching them here gets the reach across groups
    # that no router achieved, without inheriting the router's error rate.
    rel = _relations()

    plugin = _plugin_name()
    lines = []
    for r in fresh:
        say = " ".join((r.get("say") or "").split())
        when = " ".join((r.get("when") or "").split())
        name = r["skill"] if ":" in r["skill"] else f"{plugin}:{r['skill']}"
        lines.append(f"  skill: {name}")
        lines.append(f"    {say}")
        lines.append(f"    its stated trigger: {when}")
        also = [x for x in (rel.get(r["skill"]) or [])][:3]
        if also:
            lines.append(f"    often needed alongside: {', '.join(also)}")
    body = PHRASING[phase] + "\n" + "\n".join(lines)

    # NO permissionDecision. "allow" at PreToolUse skips the permission prompt,
    # so a suggestion hook that sent it approved every Write, Edit and shell
    # call it matched. additionalContext is delivered without a decision, and
    # leaving the decision out keeps the user's own permission rules in charge.
    out = {"hookEventName": event, "additionalContext": body}

    print(json.dumps({"hookSpecificOutput": out}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A suggestion is never worth breaking a tool call for.
        sys.exit(0)
