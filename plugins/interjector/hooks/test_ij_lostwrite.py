#!/usr/bin/env python3
"""Review #3: a hold must not be lost when a state write fails (Windows file locks).

In-process: the turn's marker write is made to fail with PermissionError. The hook fails open (no
output), and the hold must still be pending afterwards, not consumed and logged as delivered."""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["GNOMONKIT_HOME"] = tempfile.mkdtemp(prefix="ij-lost-")

import ij_store as S  # noqa: E402
import interject_hook as H  # noqa: E402

FAILS = []


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


S.write_note("w", "hold", "keep me", "user")
real_open = open


def failing_open(path, mode="r", *a, **k):
    if "x" in mode and str(path).endswith(".held"):
        raise PermissionError("simulated Windows lock")
    return real_open(path, mode, *a, **k)


real_replace = os.replace


def failing_replace(src, dst, *a, **k):
    if str(dst).endswith("state.json"):
        raise PermissionError("simulated Windows lock")
    return real_replace(src, dst, *a, **k)


import builtins  # noqa: E402
builtins_open = builtins.open
builtins.open = failing_open          # the per-turn marker write fails...
os.replace = failing_replace          # ...and so does any write of the shared state file
try:
    sys.stdin = io.StringIO(json.dumps({"hook_event_name": "Stop", "session_id": "w", "prompt_id": "t1",
                                        "stop_hook_active": False, "last_assistant_message": "Done."}))
    rc = H.main()
finally:
    builtins.open = builtins_open
    os.replace = real_replace
check("hook fails open when the state write fails", rc == 0)
check("the hold is still pending, not lost", len(S.pending("w", ("hold",))) == 1)

print(f"\n  {len(FAILS)} failed" if FAILS else "\n  all ok")
sys.exit(1 if FAILS else 0)
