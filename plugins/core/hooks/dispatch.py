#!/usr/bin/env python3
"""One process per hook event: run core's hooks for that event in turn, in this process.

Each hook used to be its own process (a shell, then a Python start, then the hook's imports), so one
Bash call paid for five of them. This runs the same hooks' `main()` one after another with the
event's payload as their stdin and merges what they say:

  - a block (exit 2) from any hook is a block, and it is written out at once (exit 2, its stderr
    passed on) without running the hooks after it: the event's timeout covers the whole process, so
    a slow hook later in the list must never be able to take an earlier block with it. The blocking
    hooks come first in each list for the same reason;
  - a hook that raises, or exits on its own, never stops the others or the tool call;
  - JSON output merges into one object: `hookSpecificOutput.additionalContext` and `systemMessage`
    are joined, any other key keeps the first value; plain-text stdout is passed on after it.

Which hooks run for which tool is the table below (it is the old per-matcher split of hooks.json).
The hooks stay standalone scripts; nothing here changes what one of them decides.

The event comes from the payload's `hook_event_name` (hooks.json commands take no arguments), or from
the first argument when given: dispatch.py <SessionStart|UserPromptSubmit|PreToolUse>.
"""
from __future__ import annotations

import importlib
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SHELLS = "Bash|PowerShell"
EDITS = "Write|Edit|MultiEdit|NotebookEdit"

# (hook module, tool-name pattern or None for "any"), in the order they ran as separate commands.
HOOKS = {
    "SessionStart": [
        ("session_profile", None),
        ("session_skill_map", None),
        ("lessons_inject", None),
        ("history_refresh", None),
    ],
    "UserPromptSubmit": [
        ("usage_allocation", None),
        ("correction_watch", None),
        ("plugin_freshness", None),
        ("context_watch", None),
        ("page_nudge", None),
        ("lessons_inject", None),
    ],
    "PreToolUse": [
        ("heredoc_guard", "Bash"),
        ("destructive_interlock", SHELLS),
        ("grants_guard", EDITS + "|" + SHELLS + "|SendMessage"),
        ("sync_guard", SHELLS + "|" + EDITS),
        ("skill_router", "Write|Edit|" + SHELLS),
        ("lessons_inject", EDITS),
        ("delegation_check", "Agent|Task"),
    ],
}


def matcher(event: str) -> str:
    """The hooks.json matcher for the event: every tool any of its hooks cares about (empty: all)."""
    pats = [p for _, p in HOOKS[event]]
    if None in pats:
        return ""
    seen = []
    for p in pats:
        for name in p.split("|"):
            if name not in seen:
                seen.append(name)
    return "|".join(seen)


def applies(pattern, tool: str) -> bool:
    return pattern is None or re.fullmatch(pattern, tool) is not None


def run_one(name: str, raw: str):
    """Run one hook's main() on `raw`; return (exit code, stdout, stderr). Never raises."""
    saved = sys.stdin, sys.stdout, sys.stderr, sys.argv
    out, err = io.StringIO(), io.StringIO()
    sys.stdin, sys.stdout, sys.stderr = io.StringIO(raw), out, err
    sys.argv = [os.path.join(HERE, name + ".py")]
    code = 0
    try:
        mod = importlib.import_module(name)
        rc = mod.main()
        code = rc if isinstance(rc, int) else 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:  # noqa: BLE001 - one hook's failure must not stop the others
        code = 0
    finally:
        sys.stdin, sys.stdout, sys.stderr, sys.argv = saved
    return code, out.getvalue(), err.getvalue()


def merge(outputs: list) -> str:
    """Fold the hooks' stdout into one string: one JSON object when any hook sent JSON."""
    merged: dict = {}
    contexts, messages, plain = [], [], []
    for text in outputs:
        text = text.strip()
        if not text:
            continue
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if not isinstance(obj, dict):
            plain.append(text)
            continue
        for key, val in obj.items():
            if key == "hookSpecificOutput" and isinstance(val, dict):
                hso = merged.setdefault("hookSpecificOutput", {})
                for k, v in val.items():
                    if k == "additionalContext":
                        contexts.append(str(v))
                    else:
                        hso.setdefault(k, v)
            elif key == "systemMessage":
                messages.append(str(val))
            else:
                merged.setdefault(key, val)
    if contexts:
        merged.setdefault("hookSpecificOutput", {})["additionalContext"] = "\n\n".join(contexts)
    if messages:
        merged["systemMessage"] = "\n".join(messages)
    parts = ([json.dumps(merged)] if merged else []) + plain
    return "\n".join(parts)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        raw = sys.stdin.read()
    except Exception:  # noqa: BLE001
        raw = ""
    tool, event = "", (argv[0] if argv else "")
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            tool = str(payload.get("tool_name") or "")
            event = event or str(payload.get("hook_event_name") or "")
            if not event:
                event = "PreToolUse" if tool else "UserPromptSubmit" if "prompt" in payload else ""
    except ValueError:
        pass  # unreadable payload: each hook applies its own rule (some fail closed)
    if event not in HOOKS:
        print("dispatch.py: unknown hook event %r" % event, file=sys.stderr)
        return 1

    outputs, other = [], 0
    for name, pattern in HOOKS[event]:
        if tool and not applies(pattern, tool):
            continue
        code, out, err = run_one(name, raw)
        outputs.append(out)
        if code == 2:
            text = merge(outputs)
            if text:
                sys.stdout.write(text + "\n")
            sys.stderr.write(err)
            sys.stderr.flush()
            return 2
        if err:
            sys.stderr.write(err)
        other = other or code
    text = merge(outputs)
    if text:
        sys.stdout.write(text + "\n")
    return other


if __name__ == "__main__":
    sys.exit(main())
