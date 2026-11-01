#!/usr/bin/env python3
"""UserPromptSubmit: hold each project to its share of the weekly usage budget.

The user gives projects a share of a weekly budget in ~/.gnomonkit/usage-allocation.toml (`gnomonkit
usage`, in a terminal). For a prompt in a project with a share, this counts the project's use this UTC
ISO week from the transcripts (gk_usage, cached so only new lines are read) and:

  * from 80% of the share: tells the user and the agent, once per session per week;
  * at 100%: blocks the prompt (exit 2, the reason on stderr, which Claude Code shows the user), unless
    the user lifted the block for this week with `gnomonkit usage lift`.

A prompt in a folder with no share, no allocation file or a file with a problem: nothing, and no
transcript is read. A counting error never blocks: any failure exits 0 with no output. A scan stopped by
its time limit can only under-count, so it can delay a block but never cause one.
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _gk(*args: str) -> str:
    """The full command, as the grants guard writes it: the launcher may not be on PATH."""
    script = os.path.join(os.path.dirname(HERE), "gnomonkit.py")
    return 'python "%s" ' % script + " ".join('"%s"' % a if " " in a else a for a in args)


def warn_text(alloc, st: dict, week: str) -> str:
    import gk_usage as U
    return ("GNOMONKIT usage: %s has used %d%% of its weekly share (%s of %s, %g%% of the %s budget, week %s). "
            "New prompts here stop at 100%% until the week ends (%s) or the user lifts it with `%s` in a "
            "terminal." % (st["label"], int(st["fraction"] * 100), U.fmt(st["used"], alloc.unit),
                           U.fmt(st["cap"], alloc.unit), st["share"], U.fmt(alloc.budget, alloc.unit), week,
                           U.WEEK_ENDS, _gk("usage", "lift", st["folder"])))


def block_text(alloc, st: dict, week: str) -> str:
    import gk_usage as U
    return ("GNOMONKIT usage: %s has used its weekly share: %s of %s (%g%% of the %s budget, week %s). New "
            "prompts in this project are stopped until the week ends (%s). To go on anyway, run this yourself "
            "in a terminal: `%s` (for this week), or change the share with `%s`."
            % (st["label"], U.fmt(st["used"], alloc.unit), U.fmt(st["cap"], alloc.unit), st["share"],
               U.fmt(alloc.budget, alloc.unit), week, U.WEEK_ENDS, _gk("usage", "lift", st["folder"]),
               _gk("usage", "allow", st["folder"], "<percent>")))


def decide(payload: dict, now: float | None = None):
    """("block", text), ("warn", text) or None."""
    import gk_usage as U
    now = time.time() if now is None else now
    alloc = U.load()
    if not alloc.ok or alloc.budget <= 0:
        return None
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload.get("cwd") else os.getcwd()
    project = alloc.project_for(cwd)
    if project is None:
        return None
    state = U.refresh(now)
    week = state["week"]
    st = U.status(alloc, project, U.totals(state, alloc).get(project["folder"], 0.0), week)
    if st["state"] == "blocked":
        return "block", block_text(alloc, st, week)
    if st["state"] == "warn":
        sid = str(payload.get("session_id") or "")
        if sid and sid in state["warned"]:
            return None
        if sid:
            state["warned"].append(sid)
            U.save_state(state)
        return "warn", warn_text(alloc, st, week)
    return None


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        verdict = decide(payload)
    except Exception:  # noqa: BLE001 - a counting error must never block a prompt
        return 0
    if verdict is None:
        return 0
    kind, text = verdict
    if kind == "block":
        print(text, file=sys.stderr)
        return 2
    print(json.dumps({"systemMessage": text,
                      "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
