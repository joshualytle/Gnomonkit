#!/usr/bin/env python3
"""Lessons from your history, handed to the agent at the moments they can help.

    SessionStart (startup, clear, compact)   the always-load few: pinned items, then lessons scoped
                                             everywhere that came from two or more sessions, at most 8
    UserPromptSubmit                         lessons matched to the prompt (bm25 over the register, with
                                             gates: two shared terms, a minimum idf mass, within half of
                                             the best score); other repositories' lessons dropped, this
                                             one's boosted, none repeated within a session
    PreToolUse Write|Edit|MultiEdit|NotebookEdit   once per file per session: lessons that name the file

Each item is tagged [L<id> · n sessions · this repo|everywhere] ([E<id>] for a standing entry) and
the block asks the agent to say which it used or set aside. Only ids are logged, to
~/.gnomonkit/lessons/inject.jsonl, which the effectiveness collector can set against later citations.

The register is gnomonkit_cmds/_lessons.py's (`gnomonkit lessons ...` writes it). memory.lessons in
the personal profile, or GNOMONKIT_LESSONS, is on, off or auto (the default): auto is off when
MemPalace is set up here (on PATH or registered as an MCP server), so two memory tools don't both
inject, and on otherwise; a detection that fails counts as not set up. Never blocks; any error,
exit 0 and silence.
"""
from __future__ import annotations

import json
import os
import sys
import time

CORE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
BLOCK_MAX = 3500
FOOTER = ("Lessons are priors from past sessions: really said and judged worth keeping, not necessarily tested. "
          "Check one before relying on it. In your reply, say which of these you used or set aside, by id.")
STATE_DAYS = 7


def _mods():
    if CORE not in sys.path:
        sys.path.insert(0, CORE)
    from gnomonkit_cmds import _common as C, _lessons as L, _text as T
    return C, L, T


def state_dir(C) -> str:
    return os.path.join(C.gnomonkit_home(), "lessons", "sessions")


def load_state(C, sid: str) -> dict:
    try:
        with open(os.path.join(state_dir(C), _safe(sid) + ".json"), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(C, sid: str, st: dict) -> None:
    C.atomic_write(os.path.join(state_dir(C), _safe(sid) + ".json"), json.dumps(st, sort_keys=True))
    if hash(sid) % 50 == 0:                       # now and then, drop the state of old sessions
        cutoff = time.time() - STATE_DAYS * 86400
        for f in os.listdir(state_dir(C)):
            p = os.path.join(state_dir(C), f)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.unlink(p)
            except OSError:
                pass


def _safe(sid: str) -> str:
    return "".join(ch for ch in str(sid) if ch.isalnum() or ch in "-_")[:80] or "unknown"


def block(L, header: str, items: list, repo: str) -> str:
    lines = [header]
    for i in items:
        lines.append("  %s %s" % (L.tag(i, repo), " ".join(i["text"].split())))
    lines.append(FOOTER)
    text = "\n".join(lines)
    return text if len(text) <= BLOCK_MAX else text[:BLOCK_MAX].rstrip() + " ..."


def decide(data: dict, now: float | None = None) -> tuple:
    """(event, context text or None, ids shown). Updates the session's state as a side effect."""
    C, L, T = _mods()
    cwd = data.get("cwd") or os.getcwd()
    if L.lessons_off(cwd):                    # on, off, or auto: off when MemPalace is set up here
        return None, None, []
    event = data.get("hook_event_name") or ""
    sid = str(data.get("session_id") or "")
    items, index = L.load_for_hook()          # with the token index save() wrote, when it is current
    if not items:
        return event, None, []
    repo = T.repo_name(cwd)
    st = load_state(C, sid) if sid else {}
    shown = [int(x) for x in st.get("shown") or [] if isinstance(x, int)]
    picks, header = [], ""
    if event == "SessionStart":
        if data.get("source") == "resume":
            return event, None, []               # the context already holds what was shown before
        picks = L.always_load(items, repo)
        header = "LESSONS from your history: the always-load few (pinned, or seen in two or more sessions)."
        shown = []                                # a fresh or compacted context: matches may show again
    elif event == "UserPromptSubmit":
        picks = [i for _, i in L.match_prompt(items, str(data.get("prompt") or ""), repo, shown, index=index)]
        header = "LESSONS from your history, matched to this prompt:"
    elif event == "PreToolUse" and data.get("tool_name") in EDIT_TOOLS:
        ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if not path:
            return event, None, []
        key = C.normalise_path(path if os.path.isabs(path) else os.path.join(cwd, path))
        files = [f for f in st.get("files") or [] if isinstance(f, str)]
        if key in files:
            return event, None, []
        files.append(key)
        st["files"] = files[-500:]
        picks = L.match_file(items, path, repo, index=index)
        header = "LESSONS from your history that name %s (shown once, before its first edit this session):" \
                 % os.path.basename(path)
    else:
        return event, None, []
    if sid:
        st["shown"] = sorted(set(shown) | {i["id"] for i in picks})
        try:
            save_state(C, sid, st)
        except OSError:
            pass
    if not picks:
        return event, None, []
    ids = [L.label(i) for i in picks]
    try:
        from gnomonkit_cmds import _history as H
        H.append_jsonl(os.path.join(C.gnomonkit_home(), "lessons", "inject.jsonl"),
                       {"ts": H.now_iso(now), "session": sid, "event": event, "ids": ids})
    except Exception:  # noqa: BLE001 - the log is a nicety
        pass
    return event, block(L, header, picks, repo), ids


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        if not isinstance(data, dict):
            return 0
        event, ctx, _ = decide(data)
        if ctx:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": ctx}}))
    except Exception:  # noqa: BLE001 - a hook must never break the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
