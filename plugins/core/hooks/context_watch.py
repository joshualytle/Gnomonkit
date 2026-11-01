#!/usr/bin/env python3
"""UserPromptSubmit: say when the main session's context has passed its budget, or was compacted too often.

Every turn re-reads the whole context, so a session that keeps running at a large context pays for
it on every message. This reads the last assistant turn's `usage` from the session transcript and,
when input + cache_read + cache_creation reaches the budget, tells both the agent (additionalContext)
and the user (systemMessage) to write the handoff and then /compact or start a fresh session.

Budget: the core plugin's `context_budget` option (CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET, or
GNOMONKIT_CONTEXT_BUDGET), else the personal profile's `context.context_budget`, default 250000
tokens; 0 turns the budget off. It speaks once per crossing
and again every further 100k, tracked per session under ~/.gnomonkit/context-watch (GNOMONKIT_HOME
overrides the folder). When the context drops back under the budget (after /compact) the state
resets, so the next crossing speaks again.

Compactions: auto-compaction is allowed to happen, and a single compaction gets no remark. Each one
leaves a `system` entry with subtype `compact_boundary` (or a user entry with `isCompactSummary` true),
and each one drops detail. When the count reaches the core plugin's `max_compactions` option
(CLAUDE_PLUGIN_OPTION_MAX_COMPACTIONS, or GNOMONKIT_MAX_COMPACTIONS), else the personal profile's
`context.max_compactions`, default 2, 0 = off, the agent is
told to write the session handoff (core:session-close). That cap counts automatic compactions; the
user's own /compact runs (boundary trigger "manual") count against a separate allowance, the
`manual_compactions` option (CLAUDE_PLUGIN_OPTION_MANUAL_COMPACTIONS, or GNOMONKIT_MANUAL_COMPACTIONS),
else the profile's `context.manual_compactions`, default 1. While some of it is left, the user is told
they can still /compact or start a fresh session; once it is spent, or a manual compaction goes past
it, a fresh session started from the handoff is recommended. It speaks once per count, so a further
compaction speaks again.

For the token count only the tail of the transcript is read (it seeks near the end). The compaction
count reads only the bytes added since the previous prompt (the offset lives in the state file), so a
long session is read once in total, not once per prompt. Suggest only: never blocks, no network,
exit 0 always, and any failure is silent.
"""
from __future__ import annotations

import json
import os
import re
import sys

DEFAULT_BUDGET = 250_000
DEFAULT_MAX_COMPACTIONS = 2
DEFAULT_MANUAL_COMPACTIONS = 1
STEP = 100_000
TAIL_SIZES = (64 * 1024, 1024 * 1024)  # try a small tail first; one large line can need more
CHUNK = 4 * 1024 * 1024
MARKERS = (b"compact_boundary", b"isCompactSummary")


def _profile(key: str):
    """The personal profile's value for `key` (checked against its schema), or None. Never raises."""
    try:
        core = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if core not in sys.path:
            sys.path.append(core)
        from gnomonkit_cmds import _common
        return _common.checked_profile_value(key)
    except Exception:  # noqa: BLE001 - the profile is optional
        return None


def _option(names: tuple[str, ...], default: int, profile_key: str | None = None) -> int:
    """The plugin option, else the personal profile, else `default`."""
    raw = ""
    for name in names:
        raw = (os.environ.get(name) or "").strip()
        if raw:
            break
    raw = raw.replace("_", "").replace(",", "")
    if not raw:
        v = _profile(profile_key) if profile_key else None
        return v if isinstance(v, int) else default
    try:
        return max(0, int(float(raw)))
    except ValueError:
        return default


def budget() -> int:
    return _option(("CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET", "GNOMONKIT_CONTEXT_BUDGET"), DEFAULT_BUDGET,
                   "context.context_budget")


def max_compactions() -> int:
    return _option(("CLAUDE_PLUGIN_OPTION_MAX_COMPACTIONS", "GNOMONKIT_MAX_COMPACTIONS"), DEFAULT_MAX_COMPACTIONS,
                   "context.max_compactions")


def manual_compactions() -> int:
    return _option(("CLAUDE_PLUGIN_OPTION_MANUAL_COMPACTIONS", "GNOMONKIT_MANUAL_COMPACTIONS"),
                   DEFAULT_MANUAL_COMPACTIONS, "context.manual_compactions")


def _usage_context(entry: dict) -> int | None:
    if entry.get("type") != "assistant" or entry.get("isSidechain"):
        return None
    msg = entry.get("message")
    usage = msg.get("usage") if isinstance(msg, dict) else None
    if not isinstance(usage, dict):
        return None
    total = 0
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        val = usage.get(key)
        if isinstance(val, (int, float)):
            total += int(val)
    return total if total > 0 else None


def last_context(path: str) -> int | None:
    """Context size of the last main-session assistant turn, from the tail of the transcript."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        for tail in TAIL_SIZES:
            start = max(0, size - tail)
            fh.seek(start)
            chunk = fh.read(size - start)
            lines = chunk.split(b"\n")
            if start > 0:
                lines = lines[1:]  # the first line is probably cut in half
            for line in reversed(lines):
                line = line.strip()
                if not line or b'"usage"' not in line:
                    continue
                try:
                    entry = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    ctx = _usage_context(entry)
                    if ctx is not None:
                        return ctx
            if start == 0:
                break
    return None


def _compaction_kind(line: bytes) -> tuple[int, int, int]:
    """(boundary, summary, manual): which compaction marker this main-session line is, if any."""
    try:
        entry = json.loads(line.decode("utf-8", "replace"))
    except ValueError:
        return 0, 0, 0
    if not isinstance(entry, dict) or entry.get("isSidechain"):
        return 0, 0, 0
    if entry.get("type") == "system" and entry.get("subtype") == "compact_boundary":
        meta = entry.get("compactMetadata")
        return 1, 0, int(isinstance(meta, dict) and meta.get("trigger") == "manual")
    if entry.get("isCompactSummary") is True:
        return 0, 1, 0
    return 0, 0, 0


def scan_compactions(path: str, offset: int = 0, boundaries: int = 0,
                     summaries: int = 0, manual: int = 0) -> tuple[int, int, int, int]:
    """Count compaction markers from `offset` up to the last complete line.

    Returns (new offset, boundaries, summaries, manual boundaries). A file shorter than `offset` was
    replaced, so the count starts over from the beginning. A trailing line without its newline is left
    for next time."""
    size = os.path.getsize(path)
    if offset < 0 or offset > size:
        offset, boundaries, summaries, manual = 0, 0, 0, 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        carry = b""
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            buf = carry + chunk
            cut = buf.rfind(b"\n")
            if cut < 0:
                carry = buf
                continue
            done, carry = buf[:cut + 1], buf[cut + 1:]
            offset += len(done)
            if not any(m in done for m in MARKERS):
                continue
            for line in done.split(b"\n"):
                if any(m in line for m in MARKERS):
                    b, s, m = _compaction_kind(line)
                    boundaries += b
                    summaries += s
                    manual += m
    return offset, boundaries, summaries, manual


def compactions(boundaries: int, summaries: int) -> int:
    """A compaction writes a boundary and a summary: count boundaries, or summaries when there are none."""
    return boundaries if boundaries else summaries


def _state_path(session: str) -> str:
    base = os.environ.get("GNOMONKIT_HOME") or os.path.join(os.path.expanduser("~"), ".gnomonkit")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:120] or "default"
    return os.path.join(base, "context-watch", safe + ".json")


def _read_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 -- missing or garbage state means "not notified yet"
        return {}


def _int(state: dict, key: str, default: int) -> int:
    try:
        return int(state.get(key, default))
    except (TypeError, ValueError):
        return default


def _write_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def message(ctx: int, limit: int) -> str:
    return (f"Context is at {ctx // 1000}k tokens (budget {limit // 1000}k), and every turn re-reads all of it. "
            "Finish the current step, then start no new work: write the handoff (a written brief with state, "
            "decisions and next step), then either start a fresh session from it (session-dispatch handoff) "
            "or ask the user to /compact. This is a nudge, not a block.")


def compaction_context(n: int, left: int = 0) -> str:
    if left > 0:
        then = (f"then tell the user it is written and that they can run /compact ({left} manual "
                "compaction(s) left in their allowance) or start a fresh session from it.")
    else:
        then = "then tell the user it is written and that a fresh session started from it is recommended."
    return (f"This session has been compacted {n} times, and each compaction drops detail. Before more "
            "work, write the session handoff (core:session-close: state, decisions, next step, on disk), "
            + then)


def compaction_notice(n: int, left: int = 0) -> str:
    if left > 0:
        return (f"This session has been compacted {n} times. Once Claude has written the handoff you can "
                f"/compact ({left} manual compaction(s) left) or start a fresh session from it; start a fresh "
                "session whenever you notice it forgetting things.")
    return (f"This session has been compacted {n} times. A fresh session started from the handoff Claude "
            "is writing is recommended; do the same whenever you notice it forgetting things.")


def main() -> int:
    try:
        limit = budget()
        cap = max_compactions()
        if limit <= 0 and cap <= 0:
            return 0
        data = json.loads(sys.stdin.read() or "{}")
        if not isinstance(data, dict):
            return 0
        path = data.get("transcript_path")
        if not isinstance(path, str) or not os.path.isfile(path):
            return 0
        state_path = _state_path(str(data.get("session_id") or "default"))
        state = _read_state(state_path)
        before = dict(state)
        to_agent, to_user = [], []

        if limit > 0:
            ctx = last_context(path)
            if ctx is not None:
                if ctx < limit:
                    if _int(state, "level", -1) >= 0:
                        state["level"] = -1  # back under budget: the next crossing speaks again
                else:
                    level = (ctx - limit) // STEP
                    if level > _int(state, "level", -1):
                        state["level"] = level
                        to_agent.append(message(ctx, limit))
                        to_user.append(message(ctx, limit))

        if cap > 0:
            old = _int(state, "offset", 0)
            offset, bnd, summ, man = scan_compactions(path, old, _int(state, "boundaries", 0),
                                                      _int(state, "summaries", 0), _int(state, "manual", 0))
            if offset < old:
                state["said"] = 0  # the transcript was replaced: the count started over
            state.update(offset=offset, boundaries=bnd, summaries=summ, manual=man)
            n = compactions(bnd, summ)
            allowance = manual_compactions()
            auto = n - min(man, n)
            if (auto >= cap or man > allowance) and n > _int(state, "said", 0):
                state["said"] = n
                left = max(0, allowance - man)
                to_agent.append(compaction_context(n, left))
                to_user.append(compaction_notice(n, left))

        if state != before:
            _write_state(state_path, state)
        if not to_agent:
            return 0
        print(json.dumps({"systemMessage": "GNOMONKIT: " + " ".join(to_user),
                          "hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                 "additionalContext": "GNOMONKIT: " + " ".join(to_agent)}}))
    except Exception:  # noqa: BLE001 -- a hook must never break the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
