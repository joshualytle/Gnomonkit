#!/usr/bin/env python3
"""Standalone tests for ij_store (run directly; exits non-zero on any failure)."""
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILS = []


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def fresh():
    d = tempfile.mkdtemp(prefix="ij-store-")
    os.environ["GNOMONKIT_HOME"] = d
    import importlib
    import ij_store
    importlib.reload(ij_store)
    return ij_store


S = fresh()
S.append_event("s1", {"event": "PreToolUse", "tool": "Bash", "target": "x" * 1000})
lines = (S.session_dir("s1") / "events.jsonl").read_text(encoding="utf-8").splitlines()
check("event stored with target truncated to 300", len(lines) == 1 and '"' + "x" * 300 + '"' in lines[0]
      and "x" * 301 not in lines[0])

nid = S.write_note("s1", "comment", "look at the failing test", "user")
first = S.take_notes("s1", ("comment",))
second = S.take_notes("s1", ("comment",))
check("a note is taken once", len(first) == 1 and first[0]["id"] == nid and second == [])
check("taken note is logged as delivered", "look at the failing test" in
      (S.session_dir("s1") / "delivered.jsonl").read_text(encoding="utf-8"))

S = fresh()
for i in range(20):
    S.write_note("race", "comment", f"n{i}", "watcher")
got, lock = [], threading.Lock()


def taker():
    mine = S.take_notes("race", ("comment",))
    with lock:
        got.extend(n["id"] for n in mine)


threads = [threading.Thread(target=taker) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("concurrent takers deliver each note exactly once", len(got) == 20 and len(set(got)) == 20)

check("kind filter leaves other kinds pending", (S.write_note("k", "block", "b", "user", match="rm") and
                                                 S.take_notes("k", ("comment",)) == [] and
                                                 len(S.take_notes("k", ("block",))) == 1))

check("session ids are sanitised", S.session_dir("../../etc/x").name == "etcx")

S = fresh()
now = time.time()
ok = [S.comment_budget_ok("b", now + i) for i in range(5)]
check("watcher budget: 3 per 10 minutes", ok == [True, True, True, False, False])
check("budget frees up after 10 minutes", S.comment_budget_ok("b", now + 601))

st = S.load_state("st")
st["searched"] = ["p1"]
S.save_state("st", st)
check("state round-trips", S.load_state("st")["searched"] == ["p1"])

check("labels name the source", S.label({"source": "user", "text": "t"}).startswith("INTERJECTOR (from the user")
      and S.label({"source": "watcher", "text": "t"}).startswith("INTERJECTOR (from the watcher, not the user)"))

print(f"\n  {len(FAILS)} failed" if FAILS else "\n  all ok")
sys.exit(1 if FAILS else 0)
