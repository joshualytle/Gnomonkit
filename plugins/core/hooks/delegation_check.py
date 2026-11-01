#!/usr/bin/env python3
"""PreToolUse (Agent|Task): the first time a session spawns a subagent, inject the delegation checklist.

A subagent pays off only when the context it keeps out of the main session, times the main-session
turns still to come, is more than it spends. The checklist (token-efficiency §5.3) is short; it is
shown once per session, tracked under ~/.gnomonkit/delegation-check (GNOMONKIT_HOME overrides).

When a general-purpose agent is given a prompt that reads like a search or lookup ("find", "where
is", "locate", "list", "which files"), it also suggests the read-only Explore agent; that suggestion
can appear again later in the session, once per spawn that matches.

A general-purpose or untyped spawn with no `model` inherits the parent's, usually the most expensive.
When its prompt reads like a small read-and-return job (gather, check, summarise, report) the hook
names core's cheaper `collector` agent or the `model` parameter, once per matching spawn.

Suggest only: never blocks, never edits the tool input, sends no permission decision, no network,
exit 0 always, and any failure is silent.
"""
from __future__ import annotations

import json
import os
import re
import sys

CHECKLIST = (
    "GNOMONKIT delegation check (token-efficiency 5.3), shown once per session. A subagent saves tokens "
    "only when the context it keeps out of this session, times the turns still to come here, exceeds "
    "what it spends.\n"
    "- Delegate a large investigation with a small answer, with plenty of the session left.\n"
    "- Do not delegate the last step of a task or session; do it inline.\n"
    "- Brief a bounded job with a stop-and-report point; split a big job into independent briefs.\n"
    "- Prefer read-only specialists (Explore, docs lookups, web fetch) for searching and reading.\n"
    "- Ask for a short report, with detail in files.\n"
    "- Non-token reasons are fine when stated: fresh-eyes review, parallel independent work, "
    "isolation for risky operations."
)

EXPLORE = ("GNOMONKIT: this general-purpose brief reads like a search or lookup. The read-only Explore "
           "agent (subagent_type \"Explore\") fits that better and carries less; switch unless the job "
           "also has to edit or run things.")

CHEAPER = ("GNOMONKIT: this spawn sets no model, so it inherits this session's, usually the most expensive. "
           "The brief reads like a small read-and-return job: use subagent_type \"collector\" (core, runs on "
           "haiku), or pass model \"haiku\" or \"sonnet\". Keep the inherited model for design, a hard bug "
           "or work across many files.")

_SMALL = re.compile(
    r"\b(summari[sz]e|gather|collect|check (?:that|whether|if)|report back|read[- ]only|count|tally|"
    r"extract|inventory|run (?:a few|some|the) (?:read-only )?(?:checks?|commands?)|tell me what|"
    r"return (?:a |the )?(?:short )?(?:summary|list))\b", re.I)
_HEAVY = re.compile(r"\b(design|architect|debug|root cause|investigate why|migrate|across (?:many|all) "
                    r"(?:files|modules|repos?)|review (?:this|the) (?:diff|pr|change))\b", re.I)

_LOOKUP = re.compile(
    r"\b(find|search(?: for)?|locate|look up|lookup|grep|where (?:is|are|does|do)|which (?:files?|modules?|"
    r"functions?|classes?)|list (?:all|every|the)|enumerate|what files)\b", re.I)
_DOING = re.compile(r"\b(implement|edit|write|fix|refactor|commit|push|create|build|run the tests?|merge)\b",
                    re.I)


def reads_like_lookup(prompt: str) -> bool:
    head = (prompt or "")[:1500]
    return bool(_LOOKUP.search(head)) and not _DOING.search(head)


def small_job(prompt: str) -> bool:
    head = (prompt or "")[:1500]
    return (len(prompt or "") < 1200 and bool(_SMALL.search(head) or _LOOKUP.search(head))
            and not _DOING.search(head) and not _HEAVY.search(head))


def _state_path(session: str) -> str:
    base = os.environ.get("GNOMONKIT_HOME") or os.path.join(os.path.expanduser("~"), ".gnomonkit")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:120] or "default"
    return os.path.join(base, "delegation-check", safe)


def main() -> int:
    try:
        data = json.loads(sys.stdin.read() or "{}")
        if not isinstance(data, dict) or data.get("tool_name") not in ("Agent", "Task"):
            return 0
        tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
        parts = []
        state = _state_path(str(data.get("session_id") or "default"))
        if not os.path.exists(state):
            os.makedirs(os.path.dirname(state), exist_ok=True)
            with open(state, "w", encoding="utf-8") as fh:
                fh.write("shown\n")
            parts.append(CHECKLIST)
        kind = str(tool_input.get("subagent_type") or "general-purpose").strip().lower()
        if kind == "general-purpose" and reads_like_lookup(str(tool_input.get("prompt") or "")):
            parts.append(EXPLORE)
        if kind == "general-purpose" and not str(tool_input.get("model") or "").strip() \
                and small_job(str(tool_input.get("prompt") or "")):
            parts.append(CHEAPER)
        if parts:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                     "additionalContext": "\n\n".join(parts)}}))
    except Exception:  # noqa: BLE001 -- a hook must never break the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
