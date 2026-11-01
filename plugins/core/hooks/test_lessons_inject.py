#!/usr/bin/env python3
"""lessons_inject: the always-load few at session start, matches per prompt, a file's lessons before its
first edit; never repeats, never blocks, logs ids only.

The register is synthetic, written to a temp GNOMONKIT_HOME; nothing from anyone's own setup is read."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FAILS = []


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


HOME = tempfile.mkdtemp(prefix="lessons-inject-test-")
REPO = os.path.join(HOME, "work", "demo-app")
os.makedirs(REPO)
REGISTER = os.path.join(HOME, "lessons", "register.json")
os.makedirs(os.path.dirname(REGISTER))
# memory.lessons is auto (off where MemPalace is set up): the hook sees only an empty PATH folder and an
# empty Claude config folder, so a MemPalace installed on this machine is never seen.
BIN = os.path.join(HOME, "bin")
CLAUDE = os.path.join(HOME, "claude")
os.makedirs(BIN)
os.makedirs(CLAUDE)


def item(n, text, scope="everywhere", count=1, pinned=False, kind="lesson"):
    return {"id": n, "text": text, "scope": scope, "n": count, "sources": ["s%d-%d" % (n, k) for k in range(count)],
            "pinned": pinned, "kind": kind}


with open(REGISTER, "w", encoding="utf-8") as fh:
    json.dump({"schema": 1, "items": [
        item(1, "Keep release notes to one screen.", pinned=True, kind="entry"),
        item(2, "Pin the toolchain version in the build file before upgrading any dependency.", count=3),
        item(3, "Run the database migrations against a copy before production.", scope="repo:demo-app"),
        item(4, "The release script needs the signing key loaded in the agent.", scope="repo:other-app", count=4),
        item(5, "Keep widget_store.py free of network calls.", count=1),
        item(6, "A retired lesson about the toolchain version.", count=5) | {"retired": True},
    ]}, fh)


def run(payload, raw=None, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "GNOMONKIT_"))}
    e["GNOMONKIT_HOME"] = HOME
    e["PATH"] = BIN
    e["CLAUDE_CONFIG_DIR"] = CLAUDE
    e.update(env)
    r = subprocess.run([sys.executable, os.path.join(HERE, "lessons_inject.py")], capture_output=True, text=True,
                       encoding="utf-8", input=raw if raw is not None else json.dumps(payload), env=e)
    out = json.loads(r.stdout) if r.stdout.strip() else None
    ctx = ((out or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
    return r.returncode, out, ctx


def start(source="startup", sid="sess-1"):
    return run({"hook_event_name": "SessionStart", "source": source, "session_id": sid, "cwd": REPO})


def prompt(text, sid="sess-1"):
    return run({"hook_event_name": "UserPromptSubmit", "prompt": text, "session_id": sid, "cwd": REPO})


def edit(path, sid="sess-1", tool="Edit"):
    return run({"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": sid, "cwd": REPO,
                "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}})


rc, out, ctx = start()
check("session start: the pinned entry and the lesson from 3 sessions", rc == 0 and "[E1 · entry" in ctx
      and "[L2 · 3 sessions · everywhere]" in ctx)
check("session start: one-session, other-repo and retired lessons stay out",
      "L3" not in ctx and "L4" not in ctx and "L5" not in ctx and "L6" not in ctx)
check("session start: asks which were used or set aside", "used or set aside" in ctx)
check("never a permission decision", "permissionDecision" not in json.dumps(out) and "decision" not in (out or {}))
check("the hook event is named", (out or {}).get("hookSpecificOutput", {}).get("hookEventName") == "SessionStart")

rc, out, ctx = start(source="resume")
check("resume: nothing (the context already holds it)", rc == 0 and out is None)

rc, out, ctx = prompt("run the database migrations on a copy of production first")
check("prompt: this repo's lesson matched", rc == 0 and "[L3 · 1 session · this repo]" in ctx)
rc, out, ctx = prompt("run the database migrations on a copy of production first")
check("prompt: not shown twice in a session", out is None)
rc, out, ctx = prompt("load the signing key for the release script")
check("prompt: another repo's lesson is never shown", out is None)
rc, out, ctx = prompt("ok thanks")
check("prompt: an acknowledgement matches nothing", out is None)
rc, out, ctx = prompt("run the database migrations on a copy of production first", sid="sess-2")
check("prompt: a new session can see it again", "[L3" in ctx)

target = os.path.join(REPO, "src", "widget_store.py")
rc, out, ctx = edit(target)
check("edit: the lesson naming the file, before its first edit", rc == 0 and "[L5 · 1 session" in ctx
      and "widget_store.py" in ctx)
rc, out, ctx = edit(target, tool="Write")
check("edit: once per file per session", out is None)
rc, out, ctx = edit(os.path.join(REPO, "src", "other.py"))
check("edit: a file no lesson names gets nothing", out is None)
rc, out, ctx = run({"hook_event_name": "PreToolUse", "tool_name": "Bash", "session_id": "sess-1", "cwd": REPO,
                    "tool_input": {"command": "cat widget_store.py"}})
check("other tools: nothing", rc == 0 and out is None)

with open(os.path.join(HOME, "lessons", "inject.jsonl"), encoding="utf-8") as fh:
    log = [json.loads(line) for line in fh if line.strip()]
check("the log holds ids, never the lesson text or the prompt",
      log and all(set(r) == {"ts", "session", "event", "ids"} for r in log)
      and "migrations" not in json.dumps(log) and ["L3"] in [r["ids"] for r in log])

rc, out, ctx = run({"hook_event_name": "SessionStart", "source": "startup", "session_id": "sess-3", "cwd": REPO},
                   GNOMONKIT_LESSONS="off")
check("GNOMONKIT_LESSONS=off: nothing", rc == 0 and out is None)
with open(os.path.join(HOME, "personal.toml"), "w", encoding="utf-8") as fh:
    fh.write('[memory]\nlessons = "off"\n')
rc, out, ctx = start(sid="sess-4")
check("memory.lessons = off in the profile: nothing", rc == 0 and out is None)
os.remove(os.path.join(HOME, "personal.toml"))


def forget_the_look():
    """The MemPalace look is kept for an hour per folder: drop it, so the next run looks again."""
    try:
        os.remove(os.path.join(HOME, "memory-backend.json"))
    except FileNotFoundError:
        pass


palace = os.path.join(BIN, "mempalace.cmd" if os.name == "nt" else "mempalace")
with open(palace, "w", encoding="ascii") as fh:
    fh.write("@echo off" + chr(13) + chr(10) if os.name == "nt" else "#!/bin/sh" + chr(10) + "exit 0" + chr(10))
os.chmod(palace, 0o755)
forget_the_look()
rc, out, ctx = start(sid="sess-6")
check("auto, MemPalace on PATH: nothing", rc == 0 and out is None)
rc, out, ctx = run({"hook_event_name": "SessionStart", "source": "startup", "session_id": "sess-7", "cwd": REPO},
                   GNOMONKIT_LESSONS="on")
check("GNOMONKIT_LESSONS=on beside MemPalace: the lessons again", rc == 0 and "[L2 · 3 sessions" in ctx)
with open(os.path.join(HOME, "personal.toml"), "w", encoding="utf-8") as fh:
    fh.write('[memory]' + chr(10) + 'lessons = "on"' + chr(10))
rc, out, ctx = start(sid="sess-8")
check("memory.lessons = on in the profile beside MemPalace: the lessons again", rc == 0 and "[L2 ·" in ctx)
os.remove(os.path.join(HOME, "personal.toml"))
os.remove(palace)
with open(os.path.join(CLAUDE, ".claude.json"), "w", encoding="utf-8") as fh:
    json.dump({"mcpServers": {"palace": {"command": "mempalace", "args": ["mcp"]}}}, fh)
forget_the_look()
rc, out, ctx = start(sid="sess-9")
check("auto, MemPalace registered as an MCP server only: nothing", rc == 0 and out is None)
os.remove(os.path.join(CLAUDE, ".claude.json"))
forget_the_look()
rc, out, ctx = start(sid="sess-10")
check("auto, MemPalace gone: the lessons again", rc == 0 and "[L2 ·" in ctx)

rc, out, ctx = run(None, raw="not json")
check("garbage input: exit 0, no output", rc == 0 and out is None)
with open(REGISTER, "w", encoding="utf-8") as fh:
    fh.write("{broken")
rc, out, ctx = start(sid="sess-5")
check("an unreadable register: exit 0, no output", rc == 0 and out is None)
with open(REGISTER, encoding="utf-8") as fh:
    check("an unreadable register is left as it was", fh.read() == "{broken")

shutil.rmtree(HOME, ignore_errors=True)
print(f"\n  {len(FAILS)} failed" if FAILS else "\n  all ok")
sys.exit(1 if FAILS else 0)
