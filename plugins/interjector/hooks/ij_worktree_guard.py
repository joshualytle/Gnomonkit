#!/usr/bin/env python3
"""PreToolUse hook (matcher EnterWorktree): a session that session-dispatch started never runs EnterWorktree.

EnterWorktree moves the conversation into the worktree's folder, so the session drops out of its project's
session list, where the user opens it, and the session-dispatch records stop matching it. The refusal says
what to do instead: `git worktree add` under the project's own .claude/worktrees folder, then work there by
absolute path, `git -C` and subshells, without moving the session itself.

A session counts as dispatched when any of these holds: GNOMONKIT_DISPATCH_ROLE is set (dispatch sets it for
the sessions it starts), $GNOMONKIT_HOME/dispatch/roles.json has a record for the session's id, or its
transcript's first prompt is one of dispatch's brief pointers. Anything else (the user's own sessions) is
left alone. Fails open: any error allows the call.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# dispatch.FIRST_PROMPT and RESUME_PROMPT begin so (interjector/roles.py BRIEF_POINTERS keeps the same pair).
BRIEF_POINTERS = ("Your task brief is in ", "A new brief for this session is in ")
SCAN_LINES = 200

REASON = (
    "Session dispatch started this session, and the user opens it from {project}'s session list. EnterWorktree "
    "would move this conversation into the worktree's folder, out of that list, so it is refused here. Make the "
    "worktree yourself and keep this session where it is:\n"
    "    git -C \"{project}\" fetch\n"
    "    git -C \"{project}\" worktree add \"{project}/.claude/worktrees/<name>\" -b <branch> origin/<default branch>\n"
    "Then work in it by absolute path: Read/Edit/Write with full paths, `git -C \"{project}/.claude/worktrees/<name>\" "
    "...`, and subshells `(cd \"{project}/.claude/worktrees/<name>\" && ...)` for commands that need a folder. "
    "Never `cd` the session itself. The worktree counts as {project} for the orchestration grants."
)


def _texts(content):
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                yield part["text"]


def first_prompt(transcript: str | None) -> str | None:
    """The text of the transcript's first user prompt (not a tool result), or None."""
    if not transcript or not os.path.isfile(transcript):
        return None
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i >= SCAN_LINES:
                return None
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("type") != "user":
                continue
            msg = rec.get("message")
            text = "\n".join(_texts(msg.get("content") if isinstance(msg, dict) else None)).strip()
            if text:
                return text
    return None


def dispatched(payload: dict) -> str | None:
    """Why this session counts as one dispatch started, else None."""
    if (os.environ.get("GNOMONKIT_DISPATCH_ROLE") or "").strip():
        return "GNOMONKIT_DISPATCH_ROLE"
    sid = str(payload.get("session_id") or "")
    if sid:
        import gk_grants as GR
        store = GR.roles_store()
        if sid in store or any(isinstance(r, dict) and r.get("sessionId") == sid for r in store.values()):
            return "roles record"
    first = first_prompt(payload.get("transcript_path"))
    if first and first.startswith(BRIEF_POINTERS):
        return "brief pointer"
    return None


def check(payload: dict) -> dict | None:
    if payload.get("tool_name") != "EnterWorktree" or not dispatched(payload):
        return None
    import gk_grants as GR
    folder = str(payload.get("cwd") or os.getcwd())
    project = GR.repo_root(folder) or folder
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": REASON.format(project=project)}}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        out = check(payload) if isinstance(payload, dict) else None
    except Exception:  # noqa: BLE001 - fail open
        return 0
    if out:
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
