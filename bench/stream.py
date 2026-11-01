"""Parse a `claude -p --output-format stream-json --verbose --include-hook-events` run into a trace.

Tolerant by design: a run killed mid-write leaves a truncated last line, and hook event fields have
been spelled more than one way across Claude Code versions (exit_code / exitCode, output / stdout).
Every complete line is kept; damage is reported in `error` / `truncated`, never raised.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_SKILL_LINE = re.compile(r"^\s*skill:\s*(\S+)", re.M)


@dataclass
class ToolCall:
    index: int
    name: str
    input: dict
    sub: bool


@dataclass
class HookEvent:
    name: str
    event: str
    context: str
    exit_code: int | None
    after_tool: int          # tool calls seen before this event


@dataclass
class RunTrace:
    result: str = ""
    tools: list[ToolCall] = field(default_factory=list)
    hooks: list[HookEvent] = field(default_factory=list)
    sub_texts: list[str] = field(default_factory=list)
    turns: int | None = None
    cost: float | None = None
    error: str | None = None
    truncated: bool = False
    version: str | None = None


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _hook_context(ev: dict) -> str:
    out = ev.get("output") or ev.get("stdout") or ""
    ctx = ""
    if isinstance(out, str) and out.strip().startswith("{"):
        try:
            hso = (json.loads(out) or {}).get("hookSpecificOutput") or {}
            ctx = hso.get("additionalContext") or hso.get("permissionDecisionReason") or ""
        except ValueError:
            ctx = out
    elif isinstance(out, str):
        ctx = out
    err = ev.get("stderr") or ""
    return (ctx + ("\n" + err if err else "")).strip()


def parse_stream(path: Path) -> RunTrace:
    t = RunTrace()
    try:
        lines = [ln for ln in Path(path).read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        lines = []
    saw_result = False
    for i, line in enumerate(lines):
        try:
            ev = json.loads(line)
        except ValueError:
            if i == len(lines) - 1:
                t.truncated = True
            continue
        typ = ev.get("type")
        if typ == "system" and ev.get("subtype") == "init":
            t.version = ev.get("claude_code_version")
        elif typ == "system" and ev.get("subtype") == "hook_response":
            name = ev.get("hook_name") or ""
            code = ev.get("exit_code", ev.get("exitCode"))
            t.hooks.append(HookEvent(name=name, event=ev.get("hook_event") or name.split(":")[0],
                                     context=_hook_context(ev), exit_code=_int(code), after_tool=len(t.tools)))
        elif typ == "assistant":
            sub = bool(ev.get("parent_tool_use_id"))
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "tool_use":
                    t.tools.append(ToolCall(index=len(t.tools), name=b.get("name") or "",
                                            input=b.get("input") or {}, sub=sub))
                elif b.get("type") == "text" and sub:
                    t.sub_texts.append(b.get("text") or "")
        elif typ == "result":
            saw_result = True
            t.result = ev.get("result") or ""
            t.turns = ev.get("num_turns")
            t.cost = ev.get("total_cost_usd")
            if ev.get("is_error"):
                t.error = ev.get("subtype") or "error"
    if not saw_result:
        t.error = "truncated" if t.truncated else "no-result"
    return t


def suggested_skills(e: HookEvent) -> list[str]:
    """Skills a router suggestion names (lines like '  skill: <name>')."""
    return _SKILL_LINE.findall(e.context)


def blocked(e: HookEvent) -> bool:
    return e.exit_code == 2
