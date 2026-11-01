"""Interjector watcher: reads sessions' event logs and writes watcher notes from deterministic rules.

v1 rule, repeated failure: the same command failing FAIL_LIMIT times in a session gets one comment
asking Claude to change approach or explain, once per distinct command. Model-based judging comes
later, each rule measured in Bench before it ships. Every comment respects the watcher budget
(ij_store.comment_budget_ok).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import ij_store as S  # noqa: E402

FAIL_LIMIT = 3


def _state(sid: str) -> dict:
    try:
        return json.loads((S.session_dir(sid) / "watch_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"offset": 0, "fails": {}, "flagged": []}


def _save(sid: str, st: dict) -> None:
    S._atomic_write(S.session_dir(sid) / "watch_state.json", st)


def check_session(sid: str) -> int:
    """Process new events for one session. Returns the number of notes written."""
    st = _state(sid)
    path = S.session_dir(sid) / "events.jsonl"
    if not path.is_file():
        return 0
    with open(path, "rb") as fh:
        fh.seek(st["offset"])
        chunk = fh.read()
    complete = chunk[: chunk.rfind(b"\n") + 1]          # leave a half-written last line for next time
    st["offset"] += len(complete)
    written = 0
    for line in complete.decode("utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("ok") is False and ev.get("tool") in ("Bash", "PowerShell"):
            target = ev.get("target") or ""
            st["fails"][target] = st["fails"].get(target, 0) + 1
            if st["fails"][target] >= FAIL_LIMIT and target not in st["flagged"] and S.comment_budget_ok(sid):
                shown = S.sanitize(" ".join(target.split()))[:120]     # one line, no forged labels
                S.write_note(sid, "comment",
                             f"`{shown}` has failed {st['fails'][target]} times this session. "
                             "Change approach or explain why retrying is right before running it again.",
                             "watcher")
                st["flagged"].append(target)
                written += 1
    _save(sid, st)
    return written


RETAIN_DAYS = 14
ACTIVE_S = 86400


def prune(days: int = RETAIN_DAYS) -> int:
    """Delete the Interjector's own session folders idle for longer than `days` (events, notes, guesses).
    Only folders under $GNOMONKIT_HOME/interjector that hold an events.jsonl are ever touched."""
    import shutil
    root, removed = S.home(), 0
    if not root.is_dir():
        return 0
    cutoff = time.time() - days * 86400
    for d in root.iterdir():
        ev = d / "events.jsonl"
        if d.is_dir() and ev.is_file() and ev.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def _active_sessions() -> list[str]:
    root = S.home()
    if not root.is_dir():
        return []
    now = time.time()
    return [d.name for d in root.iterdir()
            if (d / "events.jsonl").is_file() and now - (d / "events.jsonl").stat().st_mtime < ACTIVE_S]


AUTO_STOP_EVERY_S = 300
_DISPATCH = None


def auto_stop_finished() -> list:
    """session-dispatch's stop-finished: stop the subs it started that have finished (roles.finished).
    A no-op without records or with auto_stop off; never raises into the loop."""
    global _DISPATCH
    try:
        if _DISPATCH is None:
            import importlib.util
            path = Path(__file__).resolve().parents[1] / "skills" / "session-dispatch" / "dispatch.py"
            spec = importlib.util.spec_from_file_location("ij_session_dispatch", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _DISPATCH = mod
        return _DISPATCH.stop_finished()
    except Exception:  # noqa: BLE001 - the watcher's other work goes on
        return []


def run(session: str | None = None, once: bool = False, interval: float = 5.0) -> int:
    last_prune = last_auto = 0.0
    while True:
        if time.time() - last_prune > 3600:
            prune()
            last_prune = time.time()
        # The global stop of finished subs belongs to the long-running watcher, not a one-session or one-shot run.
        if not session and not once and time.time() - last_auto >= AUTO_STOP_EVERY_S:
            auto_stop_finished()
            last_auto = time.time()
        for sid in [session] if session else _active_sessions():
            check_session(sid)
        if once:
            return 0
        time.sleep(interval)
