#!/usr/bin/env python3
"""history_refresh: starts a background index run only when one is due, never prints, never fails.

Runs against a temp GNOMONKIT_HOME and an empty temp CLAUDE_CONFIG_DIR; no real index is touched and
no process is started (the spawner is replaced)."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.dirname(HERE)
FAILS = []


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


HOME = tempfile.mkdtemp(prefix="history-refresh-test-")
for k in list(os.environ):
    if k.startswith(("CLAUDE_PLUGIN_OPTION_", "GNOMONKIT_")) or k == "CLAUDE_CONFIG_DIR":
        del os.environ[k]
os.environ["GNOMONKIT_HOME"] = os.path.join(HOME, "gk")
os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(HOME, "claude")
# memory.history is auto: PATH holds only an empty folder, so a MemPalace installed here is never seen.
BIN = os.path.join(HOME, "bin")
os.makedirs(BIN)
os.environ["PATH"] = BIN
WORK = os.path.join(HOME, "work")   # the session's folder: outside any repository, no .mcp.json
os.makedirs(WORK)
sys.path.insert(0, HERE)
sys.path.insert(0, CORE)
import history_refresh  # noqa: E402
from gnomonkit_cmds import _history as H  # noqa: E402

calls = []


def fake(argv):
    calls.append(argv)


def go(spawner=fake, cwd=WORK):
    payload = {"hook_event_name": "SessionStart", "source": "startup", "cwd": cwd}
    sys.stdin = io.StringIO(json.dumps(payload))
    return history_refresh.main(spawner=spawner)


def forget_the_look():
    """mempalace_status keeps its answer for an hour per folder: drop it, so the next check looks again."""
    try:
        os.remove(os.path.join(HOME, "gk", "memory-backend.json"))
    except FileNotFoundError:
        pass


check("no index yet: due, one background run started", go() == 0 and len(calls) == 1)
argv = calls[-1]
check("it runs the core command quietly in the background",
      argv[0] == sys.executable and argv[1].endswith("gnomonkit.py") and argv[2:] == ["history", "index", "--quiet",
                                                                                     "--background"])
H.run_index()
check("a run just finished: not due, nothing started", go() == 0 and len(calls) == 1)
state = os.path.join(H.history_dir(), "state.json")
with open(state, encoding="utf-8") as fh:
    st = json.load(fh)
old = time.time() - H.REFRESH_EVERY - 60
st["started_epoch"] = old
with open(state, "w", encoding="utf-8") as fh:
    json.dump(st, fh)
check("fifteen minutes on: due again", H.due() is True and go() == 0 and len(calls) == 2)
lk = H.Lock().acquire()
check("a run holds the lock: nothing started", go() == 0 and len(calls) == 2)
lk.release()
os.environ["GNOMONKIT_HISTORY"] = "off"
check("GNOMONKIT_HISTORY=off: nothing started", go() == 0 and len(calls) == 2)
del os.environ["GNOMONKIT_HISTORY"]

# auto (the default): off where MemPalace is set up, on elsewhere
palace = os.path.join(BIN, "mempalace.cmd" if os.name == "nt" else "mempalace")
with open(palace, "w", encoding="ascii") as fh:
    fh.write("@echo off\r\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n")
os.chmod(palace, 0o755)
forget_the_look()
check("auto, MemPalace on PATH: nothing started", go() == 0 and len(calls) == 2)
os.environ["GNOMONKIT_HISTORY"] = "on"
check("GNOMONKIT_HISTORY=on beside MemPalace: started", go() == 0 and len(calls) == 3)
del os.environ["GNOMONKIT_HISTORY"]
os.remove(palace)
proj = os.path.join(WORK, "palace-app")
os.makedirs(proj)
with open(os.path.join(proj, ".mcp.json"), "w", encoding="utf-8") as fh:
    json.dump({"mcpServers": {"memory": {"command": "mempalace", "args": ["mcp"]}}}, fh)
forget_the_look()
check("auto, MemPalace registered for the session's folder only: nothing started there",
      go(cwd=proj) == 0 and len(calls) == 3)
check("auto, the same machine in another folder: started", go() == 0
      and len(calls) == 4)


def boom(argv):
    raise OSError("cannot start")


check("a spawner that raises: still exit 0", go(spawner=boom) == 0)

r = subprocess.run([sys.executable, os.path.join(HERE, "history_refresh.py")], capture_output=True, text=True,
                   input="not json", env=dict(os.environ, GNOMONKIT_HISTORY="off"))
check("as a hook: exit 0 and no output, even on garbage", r.returncode == 0 and r.stdout == "" and r.stderr == "")

sys.stdin = sys.__stdin__
shutil.rmtree(HOME, ignore_errors=True)
print(f"\n  {len(FAILS)} failed" if FAILS else "\n  all ok")
sys.exit(1 if FAILS else 0)
