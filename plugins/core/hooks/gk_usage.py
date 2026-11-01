"""Per-project usage allocation: the allocation file, the weekly count and its cache.

Shared by the UserPromptSubmit hook (usage_allocation.py) and `gnomonkit usage`. See
docs/specs/2026-10-29-usage-allocation-design.md.

The allocation file is ~/.gnomonkit/usage-allocation.toml (GNOMONKIT_HOME moves it): a weekly budget in
API-equivalent dollars or tokens, a share in percent per project folder, and lifts written by `gnomonkit
usage lift`. Use is counted from Claude Code's transcripts the way the effectiveness collector's usage
section counts it (each API message id once, its largest usage, priced with that module's table), per
UTC ISO week, and cached in ~/.gnomonkit/usage-allocation.cache.json so a prompt reads only what was
appended since the last scan.

Nothing here raises to the hook on a bad file: the loaders report a status, and the hook treats anything
but a clean count as "don't block".
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.dirname(HERE)

VERSION = 1
UNITS = ("usd", "tokens")
WARN_AT = 0.8
BLOCK_AT = 1.0
RESCAN_S = 30              # the hook rescans at most this often
SCAN_LIMIT_S = 2.0         # and stops a scan after this long, carrying on at the next prompt
CHUNK = 4 << 20            # bytes read at a time
CACHE_V = 1
WEEK_ENDS = "Monday 00:00 UTC"


# ---- where things live -------------------------------------------------------------------------
def gnomonkit_home() -> str:
    raw = (os.environ.get("GNOMONKIT_HOME") or "").strip()
    return os.path.abspath(os.path.expanduser(raw)) if raw else os.path.join(os.path.expanduser("~"), ".gnomonkit")


def alloc_path() -> str:
    return os.path.join(gnomonkit_home(), "usage-allocation.toml")


def cache_path() -> str:
    return os.path.join(gnomonkit_home(), "usage-allocation.cache.json")


def claude_projects() -> str:
    raw = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    base = os.path.abspath(os.path.expanduser(raw)) if raw else os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects")


def norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(p))))


def within(path: str, folder: str) -> bool:
    p, f = norm(path), norm(folder)
    return p == f or p.startswith(f.rstrip("\\/") + os.sep)


# ---- the week ----------------------------------------------------------------------------------
def week_id(now: float | None = None) -> str:
    y, w, _ = dt.datetime.fromtimestamp(time.time() if now is None else now, dt.timezone.utc).isocalendar()
    return "%04d-W%02d" % (y, w)


def week_start(now: float | None = None) -> float:
    d = dt.datetime.fromtimestamp(time.time() if now is None else now, dt.timezone.utc)
    monday = (d - dt.timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.timestamp()


def ts_week(ts: str) -> str | None:
    """The UTC ISO week of a transcript timestamp, or None when it doesn't parse."""
    try:
        d = dt.datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    y, w, _ = d.astimezone(dt.timezone.utc).isocalendar()
    return "%04d-W%02d" % (y, w)


# ---- the allocation file -----------------------------------------------------------------------
class Alloc:
    def __init__(self, path: str, status: str, data: dict | None = None, problems: list | None = None):
        self.path, self.status, self.data, self.problems = path, status, data or {}, problems or []

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def budget(self) -> float:
        return float((self.data.get("budget") or {}).get("weekly") or 0)

    @property
    def unit(self) -> str:
        return (self.data.get("budget") or {}).get("unit") or "usd"

    @property
    def projects(self) -> list:
        return list(self.data.get("project") or [])

    def project_for(self, folder: str):
        """The [[project]] entry whose folder holds `folder` (the longest match), or None."""
        best = None
        for p in self.projects:
            if within(folder, p["folder"]) and (best is None or len(norm(p["folder"])) > len(norm(best["folder"]))):
                best = p
        return best

    def lifted(self, folder: str, week: str) -> bool:
        return any(lf.get("week") == week and norm(lf.get("folder") or "") == norm(folder)
                   for lf in self.data.get("lift") or [])


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check(data) -> list:
    """Problems with an allocation file's contents; empty when it is fine to use."""
    if not isinstance(data, dict):
        return ["not a table"]
    problems = []
    if data.get("version", VERSION) != VERSION:
        problems.append("version %r is not %d" % (data.get("version"), VERSION))
    b = data.get("budget", {})
    if not isinstance(b, dict):
        problems.append("[budget] is not a table")
    else:
        if "weekly" in b and not (_num(b["weekly"]) and b["weekly"] >= 0):
            problems.append("budget.weekly must be a number, 0 or more")
        if b.get("unit", "usd") not in UNITS:
            problems.append("budget.unit must be one of %s" % ", ".join(UNITS))
    projects = data.get("project", [])
    total = 0.0
    seen = set()
    if not isinstance(projects, list):
        problems.append("project must be an array of tables ([[project]])")
        projects = []
    for i, p in enumerate(projects, 1):
        if not isinstance(p, dict) or not isinstance(p.get("folder"), str) or not os.path.isabs(
                os.path.expanduser(p.get("folder") or "")):
            problems.append("project %d: folder must be an absolute path" % i)
            continue
        if not (_num(p.get("share")) and 0 < p["share"] <= 100):
            problems.append("project %d: share must be a percent above 0, at most 100" % i)
            continue
        if norm(p["folder"]) in seen:
            problems.append("project %d: %s is listed twice" % (i, p["folder"]))
        seen.add(norm(p["folder"]))
        total += p["share"]
    if total > 100 + 1e-9:
        problems.append("the shares add up to %g%%, more than 100" % total)
    lifts = data.get("lift", [])
    if not isinstance(lifts, list) or any(not isinstance(x, dict) or not isinstance(x.get("folder"), str)
                                          or not isinstance(x.get("week"), str) for x in lifts):
        problems.append("lift must be an array of tables with folder and week")
    return problems


def parse_toml(text: str) -> dict:
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import gk_grants
    return gk_grants.parse_toml(text)


def load(path: str | None = None) -> Alloc:
    """status: ok, missing, unreadable, malformed or invalid."""
    path = path or alloc_path()
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return Alloc(path, "missing")
    except (OSError, UnicodeDecodeError) as e:
        return Alloc(path, "unreadable", problems=[str(e)])
    try:
        data = parse_toml(text)
    except Exception as e:  # noqa: BLE001 - any parse failure is a malformed file
        return Alloc(path, "malformed", problems=[str(e)])
    problems = check(data)
    return Alloc(path, "invalid" if problems else "ok", data, problems)


# ---- counting ----------------------------------------------------------------------------------
_EFF = None


def _effectiveness():
    """The effectiveness collector, for its price table. Imported only when a scan finds turns."""
    global _EFF
    if _EFF is None:
        d = os.path.join(CORE, "skills", "effectiveness")
        if d not in sys.path:
            sys.path.insert(0, d)
        import effectiveness
        _EFF = effectiveness
    return _EFF


def new_state(week: str) -> dict:
    return {"v": CACHE_V, "week": week, "files": {}, "turns": {}, "warned": [], "scanned_at": 0, "complete": False}


def load_state(week: str, path: str | None = None) -> dict:
    """The cache for `week`; a missing, unreadable or other-week cache starts over."""
    try:
        with open(path or cache_path(), encoding="utf-8") as fh:
            s = json.load(fh)
        if isinstance(s, dict) and s.get("v") == CACHE_V and s.get("week") == week and isinstance(
                s.get("files"), dict) and isinstance(s.get("turns"), dict):
            s.setdefault("warned", [])
            return s
    except (OSError, ValueError):
        pass
    return new_state(week)


def save_state(state: dict, path: str | None = None) -> None:
    path = path or cache_path()
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, separators=(",", ":"))
    os.replace(tmp, path)


def transcripts(projects: str, since: float) -> list:
    """Main and subagent transcripts modified since `since`: [(path, size, mtime)]."""
    out = []
    try:
        pdirs = [e.path for e in os.scandir(projects) if e.is_dir()]
    except OSError:
        return out

    def add(p):
        try:
            st = os.stat(p)
        except OSError:
            return
        if st.st_mtime >= since:
            out.append((p, st.st_size, st.st_mtime))

    for pd in pdirs:
        try:
            entries = list(os.scandir(pd))
        except OSError:
            continue
        for e in entries:
            if e.name.endswith(".jsonl") and e.is_file():
                add(e.path)
            elif e.is_dir():
                sub = os.path.join(e.path, "subagents")
                if os.path.isdir(sub):
                    for root, _dirs, files in os.walk(sub):
                        for f in files:
                            if f.startswith("agent-") and f.endswith(".jsonl"):
                                add(os.path.join(root, f))
    return out


_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def _turn(line: bytes):
    """(message id, cwd, ts, [inp, cw, cr, out, cw1h], model, speed) for an assistant line with usage, else None."""
    if b'"type":"assistant"' not in line and b'"type": "assistant"' not in line:
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("type") != "assistant":
        return None
    msg = d.get("message") if isinstance(d.get("message"), dict) else {}
    u = msg.get("usage")
    if not isinstance(u, dict):
        return None
    mid = msg.get("id") or d.get("requestId") or d.get("uuid")
    if not mid:
        return None
    nums = []
    for k in _FIELDS:
        try:
            nums.append(max(0, int(u.get(k) or 0)))
        except (TypeError, ValueError):
            nums.append(0)
    cw1h = 0
    cc = u.get("cache_creation")
    if isinstance(cc, dict):
        try:
            cw1h = max(0, int(cc.get("ephemeral_1h_input_tokens") or 0))
        except (TypeError, ValueError):
            pass
    speed = u.get("speed") if isinstance(u.get("speed"), str) else ""
    cwd = d.get("cwd") if isinstance(d.get("cwd"), str) else ""
    ts = d.get("timestamp") if isinstance(d.get("timestamp"), str) else ""
    return str(mid), cwd, ts, nums + [cw1h], str(msg.get("model") or ""), speed


def _record(state: dict, turn, fallback_cwd: str) -> None:
    mid, cwd, _ts, nums, model, speed = turn
    turns = state["turns"]
    old = turns.get(mid)
    if old is not None:
        merged = [max(a, b) for a, b in zip(old[1:6], nums)]
        if merged == old[1:6] and (old[6] or not model):
            return
        nums, cwd, model = merged, old[0] or cwd, old[6] or model
    inp, cw, cr, out, cw1h = nums
    cost = None
    if inp + cw + cr + out:
        cost = _effectiveness().turn_cost(model, inp, cw, cr, out, cw1h, speed)
    turns[mid] = [cwd or fallback_cwd, inp, cw, cr, out, cw1h, model, speed,
                  None if cost is None else round(cost, 6)]


def scan(state: dict, projects: str | None = None, now: float | None = None, limit: float | None = None) -> bool:
    """Bring `state` up to date with the transcripts. Returns True when every file was read to its end;
    False when the time limit stopped it (the offsets saved say where to carry on)."""
    now = time.time() if now is None else now
    t0 = time.monotonic()
    week = state["week"]
    files = state["files"]
    for path, size, mtime in sorted(transcripts(projects or claude_projects(), week_start(now)),
                                    key=lambda x: x[2]):
        f = files.get(path)
        if f is None or size < f["offset"]:         # new, or shrunk (replaced): read it from the start
            f = files[path] = {"offset": 0, "size": 0, "mtime": 0, "cwd": ""}
        if f["offset"] >= size:
            f["size"], f["mtime"] = size, mtime
            continue
        try:
            with open(path, "rb") as fh:
                fh.seek(f["offset"])
                while True:
                    buf = fh.read(CHUNK)
                    if not buf:
                        break
                    end = buf.rfind(b"\n")
                    if end < 0:
                        break                       # a half-written last line: next scan
                    for line in buf[:end].split(b"\n"):
                        t = _turn(line)
                        if t is None:
                            continue
                        if t[1]:
                            f["cwd"] = t[1]
                        if ts_week(t[2]) == week:
                            _record(state, t, f["cwd"])
                    f["offset"] += end + 1
                    fh.seek(f["offset"])
                    if limit is not None and time.monotonic() - t0 > limit:
                        state["complete"] = False
                        return False
        except OSError:
            continue
        f["size"], f["mtime"] = size, mtime
        if limit is not None and time.monotonic() - t0 > limit:
            state["complete"] = False
            return False
    state["scanned_at"] = now
    state["complete"] = True
    return True


def amount(row: list, unit: str) -> float:
    if unit == "tokens":
        return float(sum(row[1:5]))
    return float(row[8] or 0)


def totals(state: dict, alloc: Alloc) -> dict:
    """{project folder as written: use this week, in the budget's unit}. Turns in no allocated project
    are left out."""
    out = {p["folder"]: 0.0 for p in alloc.projects}
    where: dict = {}
    for row in state["turns"].values():
        cwd = row[0]
        if cwd not in where:
            p = alloc.project_for(cwd) if cwd else None
            where[cwd] = p["folder"] if p else None
        key = where[cwd]
        if key is not None:
            out[key] += amount(row, alloc.unit)
    return out


def refresh(now: float | None = None, limit: float | None = SCAN_LIMIT_S, force: bool = False,
            projects: str | None = None) -> dict:
    """The cache for this week, rescanned when it is older than RESCAN_S (or `force`) and saved."""
    now = time.time() if now is None else now
    state = load_state(week_id(now))
    if force or not state.get("complete") or now - float(state.get("scanned_at") or 0) >= RESCAN_S:
        scan(state, projects, now, limit)
        save_state(state)
    return state


# ---- words -------------------------------------------------------------------------------------
def fmt(v: float, unit: str) -> str:
    return "{:,} tokens".format(int(round(v))) if unit == "tokens" else "%.2f USD" % v


def label(folder: str) -> str:
    return os.path.basename(folder.rstrip("\\/")) or folder


def status(alloc: Alloc, project: dict, used: float, week: str) -> dict:
    share = float(project["share"])
    cap = alloc.budget * share / 100
    frac = used / cap if cap > 0 else 0.0
    lifted = alloc.lifted(project["folder"], week)
    state = "lifted" if lifted and frac >= BLOCK_AT else "blocked" if frac >= BLOCK_AT else \
        "warn" if frac >= WARN_AT else "ok"
    return {"folder": project["folder"], "label": label(project["folder"]), "share": share, "cap": cap,
            "used": used, "fraction": frac, "state": state, "lifted": lifted}
