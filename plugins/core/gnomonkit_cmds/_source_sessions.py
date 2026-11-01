"""History source: Claude Code transcripts, read incrementally by byte offset.

Files: <claude config>/projects/*/*.jsonl (main sessions) and */<session>/subagents/**/agent-*.jsonl
(subagents, origin "subagent"). The claude config folder is CLAUDE_CONFIG_DIR, else ~/.claude.

Indexed: a user's typed text, an assistant's text, and tool calls as the tool's name (for shell tools,
with the command). Not indexed: tool results, thinking, images, meta and compaction-summary records,
system reminders, local command output, and background-task notifications (they are not anyone's
words, and they crowd out what was said).

Each file's cursor is the byte offset after its last complete line, so a run reads only what was
appended since; a half-written last line waits for the next run. A file that got shorter than its
cursor is read again from the start (records keep their ids, so nothing is doubled).

See _history.py for the source contract this module implements.
"""
from __future__ import annotations

import glob
import json
import os
import re

NAME = "sessions"
SHELL_TOOLS = ("Bash", "PowerShell")

_DROP_BLOCKS = re.compile(r"<(system-reminder|local-command-stdout|local-command-stderr|command-message|"
                          r"task-notification|user-prompt-submit-hook)>[\s\S]*?</\1>")
_UNWRAP = re.compile(r"</?(command-name|command-args|command-contents)>")
_NOISE = re.compile(r"^\s*(\[Request interrupted by user[^\]]*\]|Caveat: The messages below were generated)", re.I)


def projects_root() -> str:
    base = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip() or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def files(root: str | None = None) -> list:
    root = root or projects_root()
    if not os.path.isdir(root):
        return []
    main = glob.glob(os.path.join(glob.escape(root), "*", "*.jsonl"))
    subs = glob.glob(os.path.join(glob.escape(root), "*", "*", "subagents", "**", "agent-*.jsonl"), recursive=True)
    return sorted(set(main) | set(subs))


def _user_text(content) -> str:
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = [b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return ""
    text = "\n".join(p for p in parts if isinstance(p, str))
    text = _UNWRAP.sub(" ", _DROP_BLOCKS.sub(" ", text)).strip()
    return "" if _NOISE.match(text) else text


def _assistant(content) -> tuple:
    if isinstance(content, str):
        return content.strip(), ""
    texts, tools = [], []
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text" and isinstance(b.get("text"), str):
            texts.append(b["text"])
        elif b.get("type") == "tool_use":
            name = str(b.get("name") or "tool")[:80]
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            cmd = inp.get("command") if name in SHELL_TOOLS else None
            tools.append("[tool: %s] %s" % (name, cmd.strip()) if isinstance(cmd, str) and cmd.strip()
                         else "[tool: %s]" % name)
    return "\n".join(texts).strip(), "\n".join(tools).strip()


def line_records(obj: dict, ctx, fallback_session: str, folder: str, subagent: bool):
    kind = obj.get("type")
    if kind not in ("user", "assistant") or obj.get("isMeta") or obj.get("isCompactSummary"):
        return
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = msg.get("content")
    uid = str(obj.get("uuid") or "")
    if not uid:
        return
    session = str(obj.get("sessionId") or fallback_session)
    cwd = str(obj.get("cwd") or "")
    project, root = ctx.project_for(cwd, folder)
    base = {"timestamp": obj.get("timestamp"), "session": session, "project": project, "root": root,
            "origin": "subagent" if (subagent or obj.get("isSidechain")) else "transcript"}
    if kind == "user":
        text = _user_text(content)
        if text:
            yield dict(base, id=uid + ":u", text=text, role="user")
        return
    text, tools = _assistant(content)
    if text:
        yield dict(base, id=uid + ":a", text=text, role="assistant")
    if tools:
        yield dict(base, id=uid + ":t", text=tools, role="tool")


def records(ctx):
    from . import _history as H
    root = projects_root()
    for path in files(root):
        key = os.path.relpath(path, root).replace("\\", "/")
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        try:
            offset = int(ctx.cursor(key, 0) or 0)
        except ValueError:
            offset = 0
        if offset > size:
            offset = 0                         # rewritten or truncated: read again; ids keep it single
        if offset == size:
            continue
        parts = key.split("/")
        folder = parts[0]
        subagent = "subagents" in parts
        fallback = parts[1] if subagent and len(parts) > 2 else os.path.splitext(parts[-1])[0]
        pos = offset
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                while pos < size:
                    line = fh.readline(H.LINE_MAX + 1)
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        if len(line) > H.LINE_MAX:   # a giant line: skip to its end without keeping it
                            while line and not line.endswith(b"\n"):
                                pos += len(line)
                                line = fh.readline(H.LINE_MAX + 1)
                            pos += len(line)
                            continue
                        break                        # half-written: the next run reads it whole
                    pos += len(line)
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(obj, dict):
                        yield from line_records(obj, ctx, fallback, folder, subagent)
        except OSError:
            continue
        if pos != offset:
            ctx.set_cursor(key, pos)
