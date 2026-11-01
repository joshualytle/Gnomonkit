"""Optional Memory tab: read-only search of your past sessions.

Two sources; the first one found wins. A `mempalace` CLI, when one is installed. Otherwise Gnomonkit
core's own local history index (~/.gnomonkit/history/history.db, built by `gnomonkit history
index`), opened read-only straight from SQLite.

Why the CLI and not the MCP server: one short-lived process per search costs nothing while idle and
loads no tool schema into any agent's context. The portal never writes to the palace or the index.

Search results are text filed from past sessions, so they are untrusted and may hold secrets that
slipped past a scrubber. Each snippet, match line and source name is capped and has token-shaped
strings masked, and the page writes them with textContent only. Unless the portal runs in plain
loopback mode (this machine only, no token, no proxy) the tab is off unless turned on explicitly
(`--memory on` or "memory": "on" in config.json), because a palace or an index is closer to raw
transcripts than anything else the portal shows.

On Windows a `.bat` or `.cmd` wrapper is never run: cmd.exe parses a batch file's arguments again,
so a search query could run commands. Point GNOMONKIT_MEMPALACE_CLI at the mempalace executable.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

QUERY_MAX = 200
RESULTS = 8
SNIPPET_MAX = 700
TIMEOUT = 60
INDEX_TERMS = 16
INDEX_WAIT_S = 1.5         # longest wait for a lock on core's history index
INDEX_BUDGET_S = 5.0       # longest a query on it may run

_HEAD = re.compile(r"^\s*\[(\d{1,3})\]\s+(.{1,120}?)\s*$")
_SRC = re.compile(r"^\s*Source:\s*(.{1,200})$")
_MATCH = re.compile(r"^\s*Match:\s*(.{1,120})$")
_RULE = re.compile(r"^\s*[-=]{8,}\s*$")
_WORD = re.compile(r"[^\W_]+")
# Long opaque runs (keys, tokens, hashes) and key=value secrets. Deliberately broad: a masked
# hash costs nothing, a leaked key costs a rotation.
_SECRETISH = [
    re.compile(r"\b[A-Za-z0-9_\-+/=]{32,}\b"),
    re.compile(r"(?i)\b(pass(word)?|secret|token|api[_-]?key|auth)\b(\s*[:=]\s*)\S+"),
]

_lock = threading.Semaphore(2)          # at most two searches at once: one process each


BATCH_REFUSED = ("a .bat or .cmd mempalace wrapper is not run (cmd.exe would parse the query as commands); "
                 "point GNOMONKIT_MEMPALACE_CLI at the mempalace executable")


def is_batch(cli) -> bool:
    return isinstance(cli, str) and cli.strip().strip('"').lower().endswith((".bat", ".cmd"))


def find_cli() -> str | None:
    env = os.environ.get("GNOMONKIT_MEMPALACE_CLI")
    if env:
        cli = env if os.path.isfile(env) else shutil.which(env)
    else:
        cli = shutil.which("mempalace")
    return None if not cli or is_batch(cli) else cli


def find_index() -> str | None:
    """Core's history database, when it exists: GNOMONKIT_HISTORY_DB, else under GNOMONKIT_HOME
    (default ~/.gnomonkit)."""
    path = os.environ.get("GNOMONKIT_HISTORY_DB") or os.path.join(
        os.environ.get("GNOMONKIT_HOME") or os.path.join(os.path.expanduser("~"), ".gnomonkit"),
        "history", "history.db")
    return path if os.path.isfile(path) else None


def mask(s: str) -> str:
    s = _SECRETISH[0].sub("[masked]", s)
    return _SECRETISH[1].sub(lambda m: m.group(1) + m.group(3) + "[masked]", s)


def _clean(q: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", q or "").strip()[:QUERY_MAX]


def parse(out: str) -> list:
    """Results from `mempalace search` text output: rank, where (wing / room), source file name, snippet."""
    res, cur = [], None
    for line in (out or "").splitlines():
        m = _HEAD.match(line)
        if m:
            cur = {"rank": int(m.group(1)), "where": m.group(2), "source": "", "match": "", "lines": []}
            res.append(cur)
            continue
        if cur is None:
            continue
        if _RULE.match(line):
            continue
        m = _SRC.match(line)
        if m and not cur["source"]:
            cur["source"] = mask(os.path.basename(m.group(1).strip())[:120])
            continue
        m = _MATCH.match(line)
        if m and not cur["match"]:
            cur["match"] = mask(m.group(1).strip())
            continue
        cur["lines"].append(re.sub(r"^\s*(→\s?)?", "", line))
    out_rows = []
    for r in res:
        snip = "\n".join(r.pop("lines")).strip()
        if len(snip) > SNIPPET_MAX:
            snip = snip[:SNIPPET_MAX].rstrip() + " ..."
        r["snippet"] = mask(snip)
        r["where"] = mask(r["where"])
        out_rows.append(r)
    return out_rows


def search(q: str, cli: str | None = None, runner=subprocess.run, results: int = RESULTS) -> dict:
    q = _clean(q)
    if not q:
        return {"query": "", "results": [], "error": None}
    cli = cli or find_cli()
    if not cli:
        return {"query": q, "results": [], "error": "mempalace CLI not found"}
    if is_batch(cli):
        return {"query": q, "results": [], "error": BATCH_REFUSED}
    if not _lock.acquire(timeout=5):
        return {"query": q, "results": [], "error": "busy, try again"}
    try:
        # "--" ends the options, so a query that starts with "-" is a query, never a flag.
        r = runner([cli, "search", "--results", str(results), "--", q], capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=TIMEOUT,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"query": q, "results": [], "error": "search timed out"}
    except OSError:
        return {"query": q, "results": [], "error": "mempalace CLI could not start"}
    finally:
        _lock.release()
    if r.returncode != 0:
        return {"query": q, "results": [], "error": "search failed (exit %s)" % r.returncode}
    return {"query": q, "results": parse(r.stdout), "error": None}


# ---- core's local history index ------------------------------------------------------------------
_COLS = "c.source, c.record, c.origin, c.session, c.project, c.role, c.ts, c.ref, c.text"


def _index_rows(con, words: list, results: int) -> list:
    import sqlite3
    has_fts = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunks_fts'").fetchone() is not None
    if has_fts:
        expr = " OR ".join('"%s"' % w.replace('"', '""') for w in words)
        try:
            return con.execute(
                "SELECT %s FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid WHERE chunks_fts MATCH ? "
                "ORDER BY bm25(chunks_fts) LIMIT ?" % _COLS, (expr, results * 10)).fetchall()
        except sqlite3.Error:
            pass                            # this Python's SQLite has no FTS5: scan instead
    like = " OR ".join("lower(c.text) LIKE ? ESCAPE '\\'" for _ in words)
    pats = ["%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for w in words]
    rows = con.execute("SELECT %s FROM chunks c WHERE %s ORDER BY c.ts DESC LIMIT 2000" % (_COLS, like),
                       pats).fetchall()
    # the most words matched first, newest first among equals
    return sorted(rows, key=lambda r: -sum(1 for w in words if w in (r[-1] or "").lower()))


def search_index(q: str, db: str | None = None, results: int = RESULTS) -> dict:
    """Ranked chunks from core's history database, one per record, read-only. The same shape as
    search(): rank, where (project and role), source (the item or the session), match (the date),
    snippet."""
    import sqlite3
    q = _clean(q)
    if not q:
        return {"query": "", "results": [], "error": None}
    db = db or find_index()
    if not db or not os.path.isfile(db):
        return {"query": q, "results": [], "error": "no history index yet (gnomonkit history index builds it)"}
    words = []
    for w in _WORD.findall(q.lower()):
        if w not in words:
            words.append(w)
    words = words[:INDEX_TERMS]
    if not words:
        return {"query": q, "results": [], "error": None}
    if not _lock.acquire(timeout=5):
        return {"query": q, "results": [], "error": "busy, try again"}
    deadline = time.monotonic() + INDEX_WAIT_S + INDEX_BUDGET_S
    try:
        # Read-only, and bounded: core's index is in WAL mode, so the indexer writing never blocks
        # this; a lock (a database from before WAL) is waited on briefly, a slow query is stopped.
        con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True, timeout=INDEX_WAIT_S)
        try:
            con.execute("PRAGMA busy_timeout = %d" % int(INDEX_WAIT_S * 1000))
            con.execute("PRAGMA query_only = ON")
            deadline = time.monotonic() + INDEX_BUDGET_S
            con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 2000)
            rows = _index_rows(con, words, results)
        finally:
            con.close()
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        busy = any(w in msg for w in ("interrupt", "locked", "busy")) or time.monotonic() > deadline
        return {"query": q, "results": [], "error": "the history index is busy or slow just now; try again"
                if busy else "the history index could not be read"}
    except sqlite3.Error:
        return {"query": q, "results": [], "error": "the history index could not be read"}
    finally:
        _lock.release()
    out, seen = [], set()
    for source, record, origin, session, project, role, ts, ref, text in rows:
        if (source, record) in seen:
            continue
        seen.add((source, record))
        snip = " ".join((text or "").split())
        if len(snip) > SNIPPET_MAX:
            snip = snip[:SNIPPET_MAX].rstrip() + " ..."
        what = role if origin == "transcript" else ("subagent " + role if origin == "subagent" and role else origin)
        out.append({"rank": len(out) + 1, "where": mask(" · ".join(x for x in (project or "?", what or "") if x)),
                    "source": mask((ref or ("session " + session[:8] if session else ""))[:120]),
                    "match": (ts or "")[:10], "snippet": mask(snip)})
        if len(out) >= results:
            break
    return {"query": q, "results": out, "error": None}
