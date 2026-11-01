"""`portal refresh`: run core's collector into the data dir, one run at a time, and record the outcome.

The collector writes its JSON into a fresh private temp folder inside the data dir (mkdtemp, 0700),
and that file replaces metrics.json only when the run succeeds, so a failed or interrupted run leaves
the last good data in place and nothing is ever written through a name someone could guess and plant.
state.json records every run (start, end, trigger, ok or the error class) so the page can say what
happened last instead of leaving the user to guess. The collector's stderr can name local paths, so
only an exception class name from its last line is kept, and only when it looks like one.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import paths

TIMEOUT = 3600            # a first run over a large history reads every transcript once
LOCK_STALE = 2 * 3600
# "ValueError", "json.decoder.JSONDecodeError", "KeyboardInterrupt": a name, never a path or a message.
_EXC_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,58}(Error|Exception|Interrupt|Exit|Expired|Warning|Iteration)")


def _now_iso(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t if t is not None else time.time()))


def state(d: Path | None = None) -> dict:
    s = paths.read_json((d or paths.data_dir()) / "state.json", {})
    return s if isinstance(s, dict) else {}


def error_class(stderr: str | None) -> str:
    """The exception class from the collector's last stderr line, or "" when that line is anything else."""
    last = ((stderr or "").strip().splitlines() or [""])[-1]
    head = last.split(":")[0].strip()
    return head if _EXC_NAME.fullmatch(head) else ""


def _lock(d: Path) -> Path | None:
    lk = d / "refresh.lock"
    for _ in range(2):
        try:
            fd = os.open(str(lk), os.O_CREAT | os.O_EXCL | os.O_WRONLY | paths.NOFOLLOW, 0o600)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return lk
        except FileExistsError:
            try:
                if time.time() - lk.stat().st_mtime > LOCK_STALE:
                    lk.unlink()
                    continue
            except OSError:
                pass
            return None
    return None


def running(d: Path | None = None) -> bool:
    lk = (d or paths.data_dir()) / "refresh.lock"
    try:
        return time.time() - lk.stat().st_mtime <= LOCK_STALE
    except OSError:
        return False


def run(trigger: str = "cli", since_days: float | None = None, collector: Path | None = None,
        d: Path | None = None, runner=subprocess.run, extra_args: list | None = None) -> dict:
    """Run the collector once. Returns the state record written to state.json."""
    d = d or paths.data_dir()
    paths.private_dir(d)
    cfg = paths.load_config(d)
    since = since_days if since_days is not None else cfg["since_days"]
    lk = _lock(d)
    if lk is None:
        return {"ok": False, "error": "already running", "trigger": trigger}
    started = time.time()
    rec = {"running": False, "started_at": _now_iso(started), "trigger": trigger}
    st: dict = {}
    work = None
    try:                                  # everything after taking the lock: the lock is always released
        st = state(d)
        paths.write_json_atomic(d / "state.json", dict(st, running=True, started_at=_now_iso(started),
                                                       trigger=trigger))
        col = collector or paths.find_collector()
        if col is None:
            rec.update({"ok": False, "error": "collector not found (install Gnomonkit core, or set "
                                              "GNOMONKIT_EFFECTIVENESS_COLLECTOR)"})
            return rec
        out = d / "metrics.json"
        work = Path(tempfile.mkdtemp(prefix=".refresh-", dir=str(d)))
        tmp = work / "metrics.json"
        cmd = [sys.executable, str(col), "--json", str(tmp), "--since", str(since), "--quiet"] + list(extra_args or [])
        try:
            r = runner(cmd, capture_output=True, text=True, timeout=TIMEOUT,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            rec.update({"ok": False, "error": "collector timed out"})
            return rec
        except OSError:
            rec.update({"ok": False, "error": "collector could not start"})
            return rec
        if r.returncode != 0 or not tmp.is_file():
            cls = error_class(r.stderr)
            rec.update({"ok": False, "error": "collector exit %s%s" % (r.returncode, (": " + cls) if cls else "")})
            return rec
        if paths.read_json(tmp) is None:
            rec.update({"ok": False, "error": "collector wrote unreadable JSON"})
            return rec
        if paths.POSIX:
            os.chmod(tmp, 0o600)          # an older collector wrote with the umask's mode
        os.replace(tmp, out)
        rec.update({"ok": True, "error": None})
        return rec
    finally:
        try:
            rec["finished_at"] = _now_iso()
            rec["seconds"] = round(time.time() - started, 1)
            if rec.get("ok"):
                rec["last_ok_at"] = rec["finished_at"]
            elif isinstance(st.get("last_ok_at"), str):
                rec["last_ok_at"] = st["last_ok_at"]
            paths.write_json_atomic(d / "state.json", rec)
        finally:
            if work is not None:
                shutil.rmtree(work, ignore_errors=True)
            try:
                lk.unlink()
            except OSError:
                pass
