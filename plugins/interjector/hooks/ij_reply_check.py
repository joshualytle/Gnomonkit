"""Reply check: a turn's final reply that is a report should have been a page.

The skill routers see prompts and tool calls. A reply that is only text makes no tool call, so the
one moment a report-pages reminder would matter, the reply itself, passes unseen. This runs at Stop
on `last_assistant_message` and the turn's own recorded tool calls: a long reply, or one with a
decisions list, in a turn that published no page, is held once with a note pointing at
core:report-pages.

Plain rules, no model. Calibrate before adding one: a rule run over past final replies should fire
on a few percent of them, and most of what it fires on should be what it names. A rule that fires
on passing mentions holds turns for nothing and gets ignored. GNOMONKIT_REPLY_CHECK=off turns it off.
"""
from __future__ import annotations

import json
import os
import re

import ij_store as S

LABEL = "INTERJECTOR (reply check, automatic): "

MIN_LINES = 30            # a reply this long is a report whatever its shape
MIN_DECISION_LINES = 12   # shorter, but with a decisions list of at least three items
FENCED_SHARE = 0.4        # mostly a fenced block (a paste-ready note, a transcript): leave it

_FENCE = re.compile(r"```.*?(```|$)", re.S)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+", re.M)
_DECISIONS = re.compile(
    r"^\s*(?:#+\s*|\*\*)?(?:for you|your (?:calls|decisions|steps|turn)|decisions?(?: for you)?|owner steps"
    r"|what you need to do|action items)\b", re.I | re.M)
# A turn that called one of these published a page: nothing to say.
_PAGE_TOOL = re.compile(r"^(?:Artifact|mcp__.*Docs__(?:batch|create|update))$")

NOTE = ("This reply reads as a report{why}, and this turn published no page. Load core:report-pages: "
        "publish it from the skill's template.html with the Artifact tool and capabilities {{db: {{}}}}, "
        "answer first, one data-ask section per decision, then reply with the link and the one-line "
        "answer. If it is not a report (text the user asked to see in chat, a paste-ready note), end "
        "the turn as it is.")


def _lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def judge(reply: str) -> str | None:
    """Why the reply reads as a report, or None."""
    if not reply:
        return None
    fenced = sum(len(m.group(0)) for m in _FENCE.finditer(reply))
    if fenced > FENCED_SHARE * len(reply):
        return None
    prose = _FENCE.sub("", reply)
    n = _lines(prose)
    if n >= MIN_LINES:
        return f" ({n} lines)"
    m = _DECISIONS.search(prose)
    if m and n >= MIN_DECISION_LINES and len(_NUMBERED.findall(prose[m.end():])) >= 3:
        return " (a list of decisions for the user)"
    return None


def turn_tools(sid: str, prompt_id: str) -> list:
    """Tools this turn called, from the session's event log."""
    out = []
    try:
        with open(S.session_dir(sid) / "events.jsonl", encoding="utf-8") as fh:
            for line in fh:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("prompt_id") == prompt_id and ev.get("event") == "PreToolUse":
                    out.append(str(ev.get("tool") or ""))
    except OSError:
        pass
    return out


def check(sid: str, prompt_id: str, reply: str) -> str | None:
    """The hold text for this reply, or None."""
    if os.environ.get("GNOMONKIT_REPLY_CHECK", "").lower() in ("off", "0", "false", "no"):
        return None
    why = judge(reply)
    if why is None:
        return None
    if any(_PAGE_TOOL.match(t) for t in turn_tools(sid, prompt_id)):
        return None
    return LABEL + NOTE.format(why=why)
