#!/usr/bin/env python3
"""Live end-to-end check of the Interjector in real Claude Code sessions.

    python live_test.py            # run all cases
    python live_test.py comment    # one case

SPENDS USAGE: each case is one short `claude -p` session (a few cents API-equivalent). Never run in CI.
Each case gets a throwaway git repo and its own Interjector store ($GNOMONKIT_HOME), queues notes for
a fixed --session-id before the session starts, then checks what actually happened: files written,
hook decisions in the stream, the final reply. Only the interjector plugin is loaded
(--setting-sources project + --plugin-dir), auto memory is off and nothing is persisted.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent
sys.path.insert(0, str(PLUGIN / "hooks"))
sys.path.insert(0, str(PLUGIN))

HISTORY = """import sys
q = " ".join(sys.argv[1:]).lower()
if "region" in q:
    print("2026-09-02, the user: new buckets go in eu-west-2 (data residency).")
"""


def _store(home: Path):
    os.environ["GNOMONKIT_HOME"] = str(home)
    import importlib
    import ij_store
    return importlib.reload(ij_store)


def _session(prompt: str, home: Path, sid: str, tools: str, extra_env: dict | None = None, turns: int = 10):
    repo = Path(tempfile.mkdtemp(prefix="ij-live-repo-"))
    (repo / "README.md").write_text("# demo\n\nA tiny demo repo.\n", encoding="utf-8")
    (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    env = {**os.environ, "GNOMONKIT_HOME": str(home), "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", **(extra_env or {})}
    cmd = ["claude", "-p", prompt, "--session-id", sid, "--setting-sources", "project",
           "--plugin-dir", str(PLUGIN), "--no-session-persistence", "--permission-mode", "acceptEdits",
           "--allowedTools", tools, "--max-turns", str(turns), "--output-format", "stream-json", "--verbose",
           "--include-hook-events"]
    p = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600)
    events = []
    for line in p.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    result = next((e for e in events if e.get("type") == "result"), {})
    return repo, events, result


def _hook_outputs(events, name_prefix):
    return [str(e.get("output") or e.get("stdout") or "") for e in events
            if e.get("type") == "system" and e.get("subtype") == "hook_response"
            and str(e.get("hook_name", "")).startswith(name_prefix)]


def case_comment():
    home = Path(tempfile.mkdtemp(prefix="ij-live-home-"))
    sid = str(uuid.uuid4())
    S = _store(home)
    S.write_note(sid, "comment", "The release code word is ZEBRA-17. Include it in your final answer.", "user")
    _, ev, res = _session("Read README.md, then tell me in one sentence what this repo is.", home, sid,
                          "Read Bash(ls:*)")
    text = res.get("result", "")
    return "ZEBRA-17" in text, f"result mentions code word: {'ZEBRA-17' in text}", res


def case_block():
    home = Path(tempfile.mkdtemp(prefix="ij-live-home-"))
    sid = str(uuid.uuid4())
    S = _store(home)
    # Match either shell tool: on Windows Claude runs commands through PowerShell, elsewhere Bash.
    S.write_note(sid, "block", "Don't list files with ls here; read README.md instead.", "user",
                 match=r"(Bash|PowerShell)\s+ls\b")
    _, ev, res = _session("Run ls to see what files are here, then tell me what this repo is.", home, sid,
                          "Read Bash(ls:*) PowerShell(ls:*)")
    denied = any('"deny"' in o and "read README.md instead" in o for o in _hook_outputs(ev, "PreToolUse"))
    return denied, f"ls denied with the user's reason: {denied}", res


def case_hold():
    home = Path(tempfile.mkdtemp(prefix="ij-live-home-"))
    sid = str(uuid.uuid4())
    S = _store(home)
    S.write_note(sid, "hold", "Before you finish: create DONE.txt containing the word ok.", "user")
    repo, ev, res = _session("Say hello in one short sentence.", home, sid, "Read Write")
    ok = (repo / "DONE.txt").is_file()
    held = any('"block"' in o for o in _hook_outputs(ev, "Stop"))
    return ok and held, f"turn held: {held}; DONE.txt written after the hold: {ok}", res


def case_watcher():
    home = Path(tempfile.mkdtemp(prefix="ij-live-home-"))
    sid = str(uuid.uuid4())
    _store(home)
    import importlib
    import interjector.watch as W
    importlib.reload(W)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            W.check_session(sid)
            time.sleep(0.5)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    try:
        _, ev, res = _session(
            "Run this exact command six times, one call at a time, and after each run say in a few words "
            "what happened: python -c \"raise SystemExit(3)\"", home, sid,
            "Bash(python:*) PowerShell(python:*)", turns=16)
    finally:
        stop.set()
        t.join(timeout=5)
    delivered = (home / "interjector" / sid / "delivered.jsonl")
    got = delivered.is_file() and '"watcher"' in delivered.read_text(encoding="utf-8")
    return got, f"watcher comment delivered into the session: {got}", res


def case_predict():
    home = Path(tempfile.mkdtemp(prefix="ij-live-home-"))
    sid = str(uuid.uuid4())
    _store(home)
    script = home / "history.py"
    script.write_text(HISTORY, encoding="utf-8")
    env = {"GNOMONKIT_HISTORY_SEARCH": json.dumps([sys.executable, str(script)])}
    _, ev, res = _session("I want a new storage bucket config. Don't write anything yet: just ask me which region "
                          "it should use.", home, sid, "Read", extra_env=env)
    text = res.get("result", "")
    held = any("predict-then-confirm" in o for o in _hook_outputs(ev, "Stop"))
    guessed = "eu-west-2" in text
    return held and guessed, f"held with history: {held}; reply uses the remembered answer: {guessed}", res


def case_pairing():
    """The user's prompt after a guess reaches the hook and is paired with it (hit-rate data)."""
    home = Path(tempfile.mkdtemp(prefix="ij-live-home-"))
    sid = str(uuid.uuid4())
    S = _store(home)
    S.log_guess(sid, {"kind": "guess", "id": "gLIVE", "question": "Which region?", "matches": "eu-west-2"})
    (S.session_dir(sid) / "open_guess").write_text("gLIVE", encoding="utf-8")
    _, ev, res = _session("Yes, eu-west-2 is right. Reply with just: noted.", home, sid, "Read")
    log = (S.session_dir(sid) / "guesses.jsonl").read_text(encoding="utf-8")
    paired = '"answers": "gLIVE"' in log and "eu-west-2 is right" in log
    return paired, f"prompt paired with the open guess: {paired}", res


CASES = {"comment": case_comment, "block": case_block, "hold": case_hold, "watcher": case_watcher,
         "predict": case_predict, "pairing": case_pairing}


def main(argv) -> int:
    names = argv or list(CASES)
    fails = 0
    for n in names:
        t0 = time.time()
        try:
            ok, detail, res = CASES[n]()
        except Exception as e:                       # a broken case is a failure, not a crash
            ok, detail, res = False, f"error: {e!r}", {}
        fails += 0 if ok else 1
        cost = res.get("total_cost_usd")
        print(f"{'PASS' if ok else 'FAIL'}  {n:<8} {detail}  ({time.time() - t0:.0f}s"
              + (f", ${cost:.3f}" if isinstance(cost, (int, float)) else "") + ")")
        if not ok and res.get("result"):
            print("        reply: " + res["result"][:300].replace("\n", " "))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
