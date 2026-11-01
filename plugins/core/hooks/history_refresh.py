#!/usr/bin/env python3
"""SessionStart: keep the local history index fresh without making session start wait.

When the last index run started more than 15 minutes ago and no run holds the lock, this starts
`gnomonkit history index --quiet --background` as a detached process and returns at once; the run
takes the lock, so parallel session starts never start two. It prints nothing, ever.

memory.history in the personal profile, or GNOMONKIT_HISTORY, is on, off or auto (the default):
auto is off when MemPalace is set up here (on PATH or registered as an MCP server) and on otherwise;
a detection that fails counts as not set up. Fails open: any error, exit 0 and silence.
"""
from __future__ import annotations

import os
import subprocess
import sys

CORE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def spawn(argv: list) -> None:
    flags = 0
    if os.name == "nt":
        # A hidden console, not DETACHED_PROCESS (see plugin_freshness.py): no window ever shows.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, creationflags=flags, start_new_session=(os.name != "nt"))


def main(spawner=spawn) -> int:
    cwd = None
    try:
        import json
        data = json.loads(sys.stdin.read() or "{}")
        cwd = data.get("cwd") if isinstance(data, dict) and isinstance(data.get("cwd"), str) else None
    except Exception:  # noqa: BLE001 - only the folder is read from the payload, and it is optional
        pass
    try:
        if CORE not in sys.path:
            sys.path.insert(0, CORE)
        from gnomonkit_cmds import _history as H
        if H.due(cwd=cwd):
            spawner([sys.executable, os.path.join(CORE, "gnomonkit.py"), "history", "index", "--quiet", "--background"])
    except Exception:  # noqa: BLE001 - a hook must never break the session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
