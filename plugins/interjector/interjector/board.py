"""Session board: every open Claude Code session on one local page, the ones waiting on you first.

Polls `claude agents --json --all` (the supported way to read session state from outside Claude Code)
and adds what git says about each session's cwd: repo, branch, whether it is a linked worktree, and
how many files are dirty. Writes one self-contained HTML file; `--watch N` rewrites it every N seconds
and the page reloads itself on the same beat. No server, nothing leaves the machine.

Last activity is the newest of: the Interjector's own event log for that session (its mtime only;
nothing in it is read) and, while watching, the moment the session's status last changed. Without
either, the page shows when the session started.

Sessions are grouped by project (a linked worktree with its repository), the group with the most
urgent session first; inside a group the primary comes first and each sub sits under its parent
(roles.arrange, from session-dispatch's roles.json). Empty sessions (an IDE tab with no prompt yet)
and finished, stopped subs are left out behind a one-line count; `--all` shows them.

Structure borrowed from pieteradejong/claude-session-monitor (MIT): stdlib only, polling, sessions
waiting on the user sorted to the top.
"""
from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import ij_naming as N  # noqa: E402
import ij_store as S  # noqa: E402

# A hook's children must never open a console window on Windows (a detached parent has none to share).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CLAUDE_TIMEOUT_S = 20
GIT_TIMEOUT_S = 5
# Sort order: what needs you first, then what is working, then what is resting, then what has ended.
RANK = {"waiting": 0, "busy": 1, "idle": 2, "ended": 3}
LABEL = {"waiting": "Needs you", "busy": "Busy", "idle": "Idle", "ended": "Ended"}


def default_out() -> Path:
    return S.home() / "board.html"


def _claude_cmd() -> list[str] | None:
    """GNOMONKIT_CLAUDE_CMD (a JSON list) overrides the executable, for tests and odd installs."""
    raw = os.environ.get("GNOMONKIT_CLAUDE_CMD")
    if raw:
        try:
            cmd = json.loads(raw)
        except ValueError:
            return None
        return [str(c) for c in cmd] if isinstance(cmd, list) and cmd else None
    exe = shutil.which("claude")
    return [exe] if exe else None


def fetch_agents() -> tuple[list[dict], str | None]:
    """(entries, error). Never raises: a missing or broken CLI is an error string, not a crash."""
    cmd = _claude_cmd()
    if not cmd:
        return [], "the claude command was not found on PATH"
    try:
        r = subprocess.run([*cmd, "agents", "--json", "--all"], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=CLAUDE_TIMEOUT_S,
                           creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return [], f"claude agents did not answer within {CLAUDE_TIMEOUT_S} s"
    except OSError as e:
        return [], f"claude agents could not start ({e.__class__.__name__})"
    if r.returncode != 0:
        return [], f"claude agents exited {r.returncode}"
    try:
        data = json.loads(r.stdout or "[]")
    except ValueError:
        return [], "claude agents printed something that is not JSON"
    if not isinstance(data, list):
        return [], "claude agents did not print a JSON array"
    return [d for d in data if isinstance(d, dict)], None


def git_info(cwd: str) -> dict:
    """Repo label, branch, worktree flag and dirty-file count for one cwd. Empty values outside git."""
    out = {"repo": None, "branch": None, "worktree": False, "dirty": None, "git": False}
    if not cwd or not os.path.isdir(cwd):
        out["repo"] = os.path.basename(os.path.normpath(cwd)) if cwd else None
        return out
    ident = N.git_identity(cwd)
    out["repo"] = N.repo_label(cwd, ident)
    if not ident:
        return out
    out.update(git=True, worktree=ident["worktree"], branch=ident["branch"], main=ident["main"])
    try:
        r = subprocess.run(["git", "-C", cwd, "status", "--porcelain=v2", "--branch", "--untracked-files=normal"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=GIT_TIMEOUT_S, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return out
    if r.returncode == 0:
        lines = r.stdout.splitlines()
        out["dirty"] = sum(1 for ln in lines if ln and not ln.startswith("#"))
        for ln in lines:
            if ln.startswith("# branch.head ") and not out["branch"]:
                head = ln[len("# branch.head "):].strip()
                out["branch"] = None if head == "(detached)" else head
    return out


def _state(entry: dict) -> str:
    status = entry.get("status")
    if status == "waiting" or entry.get("state") == "blocked":
        return "waiting"
    if status in ("busy", "idle"):
        return status
    if entry.get("state") == "working":
        return "busy"
    return "ended"                       # no live process: a finished, failed or stopped background session


def _key(entry: dict) -> str:
    return str(entry.get("sessionId") or entry.get("id") or entry.get("pid") or entry.get("cwd") or "")


def _event_mtime(sid: str | None) -> float | None:
    if not sid:
        return None
    try:
        return (S.home() / S._sid(sid) / "events.jsonl").stat().st_mtime
    except OSError:
        return None


def collect(since: dict | None = None, now: float | None = None, include_all: bool = False) -> dict:
    """One poll: the board's data. `since` (key -> [state, first seen in it]) carries status changes
    from poll to poll while watching; pass the same dict each time. Sessions are grouped by project, each
    project's primary first and its subs under their parent (roles.arrange). Empty sessions and finished,
    stopped subs are left out and counted in "hidden", unless `include_all`."""
    from interjector import roles as R
    now = time.time() if now is None else now
    entries, error = fetch_agents()
    cwds = sorted({str(e.get("cwd") or "") for e in entries})
    with ThreadPoolExecutor(max_workers=min(8, len(cwds) or 1)) as pool:
        gits = dict(zip(cwds, pool.map(git_info, cwds)))
    store = R.load()
    projects = {c: g.get("main") or (os.path.basename(os.path.normpath(c)) if c else "") for c, g in gits.items()}
    hidden = {"empty": 0, "archived": 0}
    rows = []
    for e in entries:
        cwd = str(e.get("cwd") or "")
        st = _state(e)
        _, rec = R.record_for(store, e)
        blank = R.is_empty(e, st)
        if not include_all and (blank or R.is_archived(rec, st)):
            hidden["empty" if blank else "archived"] += 1
            continue
        key = _key(e)
        changed = None
        if since is not None:
            prev = since.get(key)
            if not prev or prev[0] != st:
                since[key] = [st, now]
            changed = since[key][1] if prev else None     # first sight is not a change
        started = e.get("startedAt") / 1000 if isinstance(e.get("startedAt"), (int, float)) else None
        stamps = [t for t in (_event_mtime(e.get("sessionId")), changed) if t]
        rows.append({
            "key": key, "name": e.get("name"), "kind": e.get("kind"), "state": st,
            "status": e.get("status"), "jobState": e.get("state"), "waitingFor": e.get("waitingFor"),
            "pid": e.get("pid"), "sessionId": e.get("sessionId"), "id": e.get("id"), "cwd": cwd,
            "startedAt": started, "lastActivity": max(stamps) if stamps else None,
            "stateSince": since[key][1] if since is not None and key in since else None,
            **gits.get(cwd, {}),
            "role": R.role_of(e, rec), "parent": (rec or {}).get("parent"), "empty": blank,
            "project": R.project_label(cwd, rec, projects),
        })
    if since is not None:
        for gone in set(since) - {r["key"] for r in rows}:
            del since[gone]
    rows = R.arrange(rows, key=lambda r: (RANK[r["state"]],
                                          (r["stateSince"] or r["lastActivity"] or r["startedAt"] or now)
                                          if r["state"] == "waiting" else -(r["lastActivity"] or r["startedAt"] or 0)))
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in RANK}
    return {"generated": now, "error": error, "counts": counts, "sessions": rows, "hidden": hidden}


def _ago(t: float | None, now: float) -> str:
    if not t:
        return ""
    s = max(0, int(now - t))
    if s < 60:
        return f"{s} s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h {s % 3600 // 60} min ago"
    return f"{s // 86400} d ago"


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{refresh}<title>Session Board</title>
<meta name="description" content="Open Claude Code sessions: which need you, which are working, which are idle.">
<style>
:root {{
  --bg: #fbfaf7; --fg: #1d1d1b; --muted: #66645e; --line: #e2dfd7; --card: #ffffff;
  --accent: #0f766e; --accent-fg: #ffffff; --chip: #f1efe9; --warn: #9a3412;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --bg: #161614; --fg: #ecebe6; --muted: #a3a19a; --line: #2e2d29; --card: #1e1e1b;
    --accent: #2dd4bf; --accent-fg: #0b1f1d; --chip: #2a2925; --warn: #fdba74;
  }}
}}
:root[data-theme="dark"] {{
  --bg: #161614; --fg: #ecebe6; --muted: #a3a19a; --line: #2e2d29; --card: #1e1e1b;
  --accent: #2dd4bf; --accent-fg: #0b1f1d; --chip: #2a2925; --warn: #fdba74;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main, header {{ max-width: 760px; margin: 0 auto; padding: 0 16px; }}
header {{ padding-top: 24px; }}
h1 {{ font-size: 1.5rem; line-height: 1.25; margin: 0 0 4px; }}
.meta {{ color: var(--muted); margin: 0 0 16px; font-size: .9rem; }}
.meta.warn {{ color: var(--warn); }}
section {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 12px 16px; margin: 0 0 12px; }}
section.waiting {{ border-color: var(--warn); border-width: 2px; }}
section.ended {{ opacity: .7; }}
.top {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; justify-content: space-between; }}
.title {{ font-weight: 600; overflow-wrap: anywhere; }}
.chip {{ display: inline-block; font-size: .8rem; padding: 2px 10px; border-radius: 999px; background: var(--chip); white-space: nowrap; }}
.chip.waiting {{ background: var(--warn); color: var(--card); font-weight: 600; }}
.chip.busy {{ background: var(--accent); color: var(--accent-fg); }}
.why {{ color: var(--warn); font-weight: 600; margin-top: 4px; }}
.facts {{ color: var(--muted); font-size: .9rem; margin-top: 4px; overflow-wrap: anywhere; }}
.facts b {{ color: var(--fg); font-weight: 500; }}
.path {{ font: .8rem/1.4 ui-monospace, Consolas, monospace; color: var(--muted); overflow-wrap: anywhere; margin-top: 2px; }}
.empty {{ color: var(--muted); }}
h2.group {{ font-size: 1rem; margin: 20px 0 8px; color: var(--muted); font-weight: 600; }}
section.d1 {{ margin-left: 24px; }}
section.d2 {{ margin-left: 48px; }}
.chip.role {{ background: transparent; border: 1px solid var(--line); }}
</style></head><body>
<header>
  <h1>{headline}</h1>
  <p class="meta">{summary}</p>
  {error}
</header>
<main>
{cards}
</main>
</body></html>
"""


def _card(r: dict, now: float) -> str:
    e = html.escape
    title = r.get("name") or r.get("repo") or r.get("cwd") or "session"
    chip = LABEL[r["state"]] + (" · background" if r.get("kind") == "background" else "")
    since = r.get("stateSince")
    why = ""
    if r["state"] == "waiting":
        what = r.get("waitingFor") or "something from you"
        why = f'<div class="why">Waiting for: {e(str(what))}{" · " + e(_ago(since, now)) if since else ""}</div>'
    facts = []
    if r.get("repo"):
        facts.append(f"<b>{e(str(r['repo']))}</b>")
    if r.get("git"):
        facts.append(f"branch <b>{e(str(r.get('branch') or 'detached'))}</b>")
        facts.append("worktree" if r.get("worktree") else "main checkout")
        if r.get("dirty") is not None:
            facts.append("clean" if r["dirty"] == 0 else f"{r['dirty']} dirty")
    elif r.get("cwd"):
        facts.append("not a git repo")
    if r.get("lastActivity"):
        facts.append(f"last activity {e(_ago(r['lastActivity'], now))}")
    elif r.get("startedAt"):
        facts.append(f"started {e(_ago(r['startedAt'], now))}")
    if r.get("jobState") and r["state"] == "ended":
        facts.append(e(str(r["jobState"])))
    role = r.get("role")
    if role:
        parent = r.get("parent")
        role_chip = f'<span class="chip role">{e(role + (" of " + str(parent) if role == "sub" and parent else ""))}</span> '
    else:
        role_chip = ""
    if r.get("empty"):
        facts.append("empty (no prompt yet)")
    depth = min(2, int(r.get("depth") or 0))
    return (f'<section class="{r["state"]}{" d" + str(depth) if depth else ""}"><div class="top">'
            f'<span class="title">{e(str(title))}</span>'
            f'<span>{role_chip}<span class="chip {r["state"]}">{e(chip)}</span></span></div>{why}'
            f'<div class="facts">{" · ".join(facts)}</div>'
            f'<div class="path">{e(r.get("cwd") or "")}</div></section>')


def render(board: dict, refresh: int | None = None) -> str:
    now = board["generated"]
    c = board["counts"]
    rows = board["sessions"]
    n_wait = c.get("waiting", 0)
    headline = (f"{n_wait} session{'s' if n_wait != 1 else ''} need{'s' if n_wait == 1 else ''} you"
                if n_wait else "Nothing is waiting on you")
    parts = [f"{len(rows)} open", f"{c.get('busy', 0)} busy", f"{c.get('idle', 0)} idle"]
    if c.get("ended"):
        parts.append(f"{c['ended']} ended")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    summary = " · ".join(parts) + f" · as of {stamp}" + (f" · refreshes every {refresh} s" if refresh else "")
    error = f'<p class="meta warn">Could not read sessions: {html.escape(board["error"])}</p>' if board.get("error") else ""
    n_empty = (board.get("hidden") or {}).get("empty", 0)
    if n_empty:
        error += (f'<p class="meta">{n_empty} empty tab{"s" if n_empty != 1 else ""}: close them in your IDE '
                  "(<code>board --all</code> shows them)</p>")
    out, group = [], None
    multi = len({str(r.get("project") or "") for r in rows}) > 1
    for r in rows:
        g = str(r.get("project") or "")
        if multi and g != group:
            out.append(f'<h2 class="group">{html.escape(g or "no folder")}</h2>')
            group = g
        out.append(_card(r, now))
    cards = "\n".join(out) or '<p class="empty">No open sessions.</p>'
    meta = f'<meta http-equiv="refresh" content="{int(refresh)}">\n' if refresh else ""
    return PAGE.format(refresh=meta, headline=html.escape(headline), summary=html.escape(summary),
                       error=error, cards=cards)


def write_page(text: str, out: Path) -> Path:
    """Atomic replace, so a browser reloading mid-write never shows half a page."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=".board-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    for attempt in range(6):
        try:
            os.replace(tmp, out)
            return out
        except PermissionError:                     # Windows: a reader has the file open for a moment
            if attempt == 5:
                os.unlink(tmp)
                raise
            time.sleep(0.05 * (attempt + 1))
    return out


def run(out: Path | None = None, watch: float | None = None, as_json: bool = False,
        max_polls: int | None = None, include_all: bool = False) -> int:
    out = Path(out) if out else default_out()
    since: dict = {}
    polls = 0
    while True:
        board = collect(since if watch else None, include_all=include_all)
        if as_json:
            sys.stdout.write(json.dumps(board, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        else:
            write_page(render(board, refresh=max(1, int(round(watch))) if watch else None), out)
            if polls == 0:
                print(f"board: {out}")
            if board["error"]:
                print(f"board: {board['error']}", file=sys.stderr)
        polls += 1
        if not watch or (max_polls is not None and polls >= max_polls):
            return 0
        time.sleep(watch)
