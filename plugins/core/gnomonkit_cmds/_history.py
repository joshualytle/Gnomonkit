"""Gnomonkit's local history index: past sessions, searchable with nothing but Python and SQLite.

    ~/.gnomonkit/history/            (GNOMONKIT_HOME moves it; the folder is 0700, files 0600 on POSIX)
      history.db                     SQLite: chunks, their FTS5 index (porter unicode61), source cursors
      state.json                     the last index run: when, how long, what it read, what it dropped
      search.json                    {"argv": [...]} that runs a search; other plugins find core through it
      queries.jsonl / marks.jsonl    one line per search (id, counts, never the words) / per `history mark`
      index.lock                     held by the one index run allowed at a time

SOURCES (the seam). The index never parses a format itself. A source is a module in this package
named `_source_<name>.py` that defines

    NAME = "<name>"                  the source's key (cursors and rows are kept per source)
    def records(ctx): ...            yields records, see below
    def enabled(args) -> bool        optional: run this time? (default: always). `args` is the parsed
                                     `history index` arguments, or None for the session-start refresh
    def add_arguments(parser)        optional: flags of its own on `gnomonkit history index`

A record is a dict with the four keys every source must give,

    id         stable text id, unique within the source; a record with an id already in the index
               replaces it (so a source may re-yield an edited item)
    text       the text to index (scrubbed and chunked here, never by the source)
    timestamp  ISO 8601 text or epoch seconds
    origin     a short type shown in results ("transcript", "subagent", "work", ...)

and may add project (display name), root (repository folder, for --project here and the repo
bonus), session, role, ref (shown in results, e.g. "owner/repo#12") and url. A source that reads
incrementally keeps its place with ctx.cursor(key) / ctx.set_cursor(key, value). Records and
cursors are committed together every BATCH writes and at the end of the source, so a crash loses at
most the uncommitted batch, which is read again from the last committed cursor; since a record
replaces the one with its id, nothing is doubled. ctx.project_for(cwd) gives (name, root) for a
working directory, and ctx.args the command's arguments. _source_sessions.py (Claude Code
transcripts) is the built-in source; a new one (GitHub work items, say) is a new file and this
module does not change.

Every record passes the scrubber before anything is stored (whole, then cut to RECORD_MAX); a
record the scrubber raises on is dropped and counted, never stored raw. Search output is scrubbed
again (rules may have been added since). If this Python's SQLite has no FTS5, the index still
builds and search falls back to a slower LIKE scan scored with the same bm25 formula; `history
status` and every search say so.

The file is in WAL mode and readers open it read-only (Store(readonly=True)), so a search runs
while the indexer writes. Each run prunes by age and size (retention(), prune()).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import time

from . import _common as C
from . import _text as T

CHUNK = 800                 # characters per chunk, about
OVERLAP = 120               # characters repeated at the start of the next chunk
RECORD_MAX = 16000          # a longer record (a pasted log) is cut here, after scrubbing, before chunking
RAW_MAX = 4 * RECORD_MAX    # the scrubber sees this much of a record at most, ending at a whole word
LINE_MAX = 4 * 1024 * 1024  # a transcript line longer than this is skipped unparsed (a giant tool result)
BATCH = 500                 # records the indexer writes between commits
READ_WAIT_MS = 1500         # a reader waits this long at most for a lock, then fails open
WRITE_WAIT_MS = 5000        # the indexer and forget wait this long
KEEP_DAYS = 365             # memory.history_keep_days: rows older than this are pruned (0 keeps them all)
MAX_MB = 1024               # memory.history_max_mb: the oldest rows go when the data passes this (0: no cap)
SEARCH_BUDGET_S = 2.0       # the hooks' search (search.json) stops its query after this long
REFRESH_EVERY = 15 * 60     # the session-start refresh runs when the last run started longer ago than this
LOCK_STALE = 20 * 60        # a lock not touched for this long belongs to a run that died
REPO_BONUS = 1.5            # score factor for this repository's chunks: reorders, never drops
SNIPPET = 420               # characters of each result shown
OUTPUT_MAX = 6000           # characters of a whole search printed
LIKE_CANDIDATES = 5000      # newest matching chunks the LIKE fallback scores
SCHEMA_VERSION = 1


# ---- where things live -------------------------------------------------------------------------
def history_dir() -> str:
    return os.path.join(C.gnomonkit_home(), "history")


def db_path() -> str:
    return os.path.join(history_dir(), "history.db")


def mode(cwd: str | None = None) -> tuple:
    """(on, why): whether indexing runs here. GNOMONKIT_HISTORY, then memory.history, then auto (off
    when MemPalace is set up here); see _common.memory_feature. Never raises."""
    return C.memory_feature("memory.history", "GNOMONKIT_HISTORY", cwd)


def disabled(cwd: str | None = None) -> bool:
    return not mode(cwd)[0]


def _path(name: str) -> str:
    return os.path.join(history_dir(), name)


def now_iso(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if t is None else t))


def iso(ts) -> str:
    """A record's timestamp as UTC ISO text to the second ("" when it can't be read)."""
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return now_iso(ts / 1000.0 if ts > 1e11 else ts)
    s = str(ts or "").strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}(?::\d{2})?))?", s)
    if not m:
        return ""
    hms = m.group(2) or "00:00:00"
    return "%sT%sZ" % (m.group(1), hms if len(hms) == 8 else hms + ":00")


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def append_jsonl(path: str, row: dict) -> None:
    C.ensure_dir(os.path.dirname(path))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


# ---- SQLite ------------------------------------------------------------------------------------
_FTS5 = None


def fts5_available() -> bool:
    """True when this interpreter's SQLite can make an FTS5 table."""
    global _FTS5
    if _FTS5 is None:
        import sqlite3
        try:
            con = sqlite3.connect(":memory:")
            con.execute("CREATE VIRTUAL TABLE t USING fts5(a, tokenize='porter unicode61')")
            con.close()
            _FTS5 = True
        except sqlite3.Error:
            _FTS5 = False
    return _FTS5


_DDL = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS cursors(source TEXT NOT NULL, key TEXT NOT NULL, value TEXT, PRIMARY KEY(source, key));
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY, source TEXT NOT NULL, record TEXT NOT NULL, seq INTEGER NOT NULL,
  origin TEXT, session TEXT, project TEXT, root TEXT, role TEXT, ts TEXT, ref TEXT, url TEXT, text TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_record ON chunks(source, record);
CREATE INDEX IF NOT EXISTS chunks_session ON chunks(session);
CREATE INDEX IF NOT EXISTS chunks_project ON chunks(project);
CREATE INDEX IF NOT EXISTS chunks_ts ON chunks(ts);
"""
_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id',
  tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text); END;
"""


class IndexError_(RuntimeError):
    """The index can't be used as it is; the message says what to do."""


_NO_FTS5_HERE = ("this index was built with SQLite's FTS5, which this Python lacks. Run the command with the "
                 "Python that built it, or `gnomonkit history forget --all --yes` and `gnomonkit history index "
                 "--rebuild` to rebuild it without FTS5.")


class Store:
    """The SQLite file, in WAL mode: a search reads while the indexer writes, and neither waits.

    Read-write (the default) is for the indexer and `history forget`. Everything that only reads
    (search, status, `lessons candidates`, the hooks through them) opens it with readonly=True: the
    file is opened read-only, nothing is written (not even the schema), the full-text index is never
    built, and a lock is waited on for READ_WAIT_MS at most. Building the full-text index over rows
    that are already there (a database made by a Python without FTS5) happens only when the indexer
    asks for it with upgrade=True."""

    def __init__(self, path: str | None = None, create: bool = True, use_fts: bool | None = None,
                 readonly: bool = False, upgrade: bool = False):
        import sqlite3
        self.sqlite3 = sqlite3
        self.path = path or db_path()
        self.readonly = readonly
        self._pending = 0
        if readonly:
            self._open_readonly()
            return
        if not os.path.exists(self.path):
            if not create:
                raise FileNotFoundError(self.path)
            C.ensure_dir(os.path.dirname(self.path))
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600))   # 0600 before SQLite writes a byte
        if os.name != "nt":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        self.con = sqlite3.connect(self.path, timeout=WRITE_WAIT_MS / 1000.0)
        try:
            self.con.execute("PRAGMA busy_timeout = %d" % WRITE_WAIT_MS)
            try:
                self.con.execute("PRAGMA journal_mode = WAL")   # kept in the file: readers never wait on a writer
                self.con.execute("PRAGMA journal_size_limit = %d" % (64 * 1024 * 1024))
            except sqlite3.OperationalError:
                pass                                          # busy now; the next writer switches it
            self.con.execute("PRAGMA secure_delete = ON")       # forget overwrites, not just unlinks pages
            self.con.executescript(_DDL)
            has = self.con.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunks_fts'").fetchone() is not None
            want = fts5_available() if use_fts is None else (use_fts and fts5_available())
            if has and not fts5_available():
                raise IndexError_(_NO_FTS5_HERE)
            if want and not has:
                empty = self.con.execute("SELECT 1 FROM chunks LIMIT 1").fetchone() is None
                if empty or upgrade:
                    self.con.executescript(_FTS_DDL)
                    if not empty:
                        self.con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
                    has = True
            self.fts = has
            self.con.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
            self.con.execute("INSERT OR REPLACE INTO meta VALUES ('fts', ?)", ("fts5" if self.fts else "like",))
            self.con.commit()
        except BaseException:
            self.close()
            raise

    def _open_readonly(self) -> None:
        sqlite3 = self.sqlite3
        if not os.path.isfile(self.path):
            raise FileNotFoundError(self.path)
        from pathlib import Path
        uri = Path(self.path).resolve().as_uri() + "?mode=ro"
        self.con = sqlite3.connect(uri, uri=True, timeout=READ_WAIT_MS / 1000.0)
        try:
            self.con.execute("PRAGMA busy_timeout = %d" % READ_WAIT_MS)
            self.con.execute("PRAGMA query_only = ON")
            names = {r[0] for r in self.con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        except BaseException:
            self.close()
            raise
        if "chunks" not in names:
            self.close()
            raise FileNotFoundError(self.path)                # made, but nothing indexed yet
        if "chunks_fts" in names and not fts5_available():
            self.close()
            raise IndexError_(_NO_FTS5_HERE)
        self.fts = "chunks_fts" in names

    def close(self) -> None:
        try:
            self.con.close()
        except Exception:
            pass

    def commit(self) -> None:
        self.con.commit()
        self._pending = 0

    def tick(self, n: int = 1) -> bool:
        """Count n written records; commit once BATCH have built up, so a long run shows its progress
        and never holds the write lock for long. True when it committed."""
        self._pending += n
        if self._pending >= BATCH:
            self.commit()
            return True
        return False

    # cursors
    def cursor(self, source: str, key: str, default=None):
        row = self.con.execute("SELECT value FROM cursors WHERE source = ? AND key = ?", (source, key)).fetchone()
        return row[0] if row else default

    def set_cursor(self, source: str, key: str, value) -> None:
        """Saved in the same transaction as the records yielded before it (see the module docstring)."""
        self.con.execute("INSERT OR REPLACE INTO cursors VALUES (?, ?, ?)", (source, key, str(value)))
        self.tick()

    # rows
    def replace_record(self, source: str, rec: dict, chunks: list) -> int:
        self.con.execute("DELETE FROM chunks WHERE source = ? AND record = ?", (source, rec["id"]))
        for i, text in enumerate(chunks):
            self.con.execute(
                "INSERT INTO chunks(source, record, seq, origin, session, project, root, role, ts, ref, url, text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source, rec["id"], i, rec["origin"], rec.get("session") or "", rec.get("project") or "",
                 rec.get("root") or "", rec.get("role") or "", rec["ts"], rec.get("ref") or "", rec.get("url") or "",
                 text))
        return len(chunks)

    def count(self, where: str = "", params=()) -> int:
        return int(self.con.execute("SELECT count(*) FROM chunks " + where, params).fetchone()[0])

    def delete(self, where: str, params=()) -> int:
        n = self.count("WHERE " + where, params)
        self.con.execute("DELETE FROM chunks WHERE " + where, params)
        self.con.commit()
        return n

    def vacuum(self) -> None:
        """Rewrite the file without the deleted rows, then empty the write-ahead log, which can still
        hold copies of the pages they were on."""
        if self.fts:
            self.con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('optimize')")
            self.con.commit()
        self.con.execute("VACUUM")
        try:
            self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        except self.sqlite3.OperationalError:
            pass


# ---- sources -----------------------------------------------------------------------------------
def source_modules() -> list:
    """The `_source_<name>.py` modules of this package that define NAME and records(ctx), by name."""
    import importlib
    import pkgutil
    pkg = importlib.import_module(__package__ or "gnomonkit_cmds")
    out = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if not info.name.startswith("_source_"):
            continue
        try:
            mod = importlib.import_module(pkg.__name__ + "." + info.name)
        except Exception:  # noqa: BLE001 - one broken source must not stop the others
            continue
        if isinstance(getattr(mod, "NAME", None), str) and callable(getattr(mod, "records", None)):
            out.append(mod)
    return out


def _as_list(v) -> list:
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, (list, tuple)) else []


class Exclusions:
    """Projects that are never indexed: [memory] exclude_projects in the personal profile (names, or
    folder paths), and any repository whose root holds a .gnomonkit-no-history file."""

    MARKER = ".gnomonkit-no-history"

    def __init__(self, entries=None):
        raw = C.profile_value("memory.exclude_projects") if entries is None else entries
        self.names, self.paths = set(), []
        for e in _as_list(raw or []):
            if any(sep in e for sep in ("/", "\\")) or e.startswith("~"):
                self.paths.append(e)
            else:
                self.names.add(e.casefold())
        self._marker: dict = {}

    def __call__(self, project: str, root: str) -> bool:
        if project and project.casefold() in self.names:
            return True
        if root:
            if any(C.path_within(root, p) for p in self.paths):
                return True
            if root not in self._marker:
                self._marker[root] = os.path.isfile(os.path.join(root, self.MARKER))
            return self._marker[root]
        return False


class Context:
    """What a source sees: its cursors, the project of a folder, the command's arguments."""

    def __init__(self, store: Store, name: str, args=None, touch=None):
        self.store, self.name, self.args = store, name, args
        self._touch = touch or (lambda: None)
        self._projects: dict = {}

    def cursor(self, key: str, default=None):
        return self.store.cursor(self.name, key, default)

    def set_cursor(self, key: str, value) -> None:
        self.store.set_cursor(self.name, key, value)
        self._touch()

    def project_for(self, cwd: str, fallback: str = "") -> tuple:
        """(project name, repository root key) for a working directory. The root key is the
        normalised folder (case-folded on Windows), "" when the folder is unknown."""
        if cwd in self._projects:
            return self._projects[cwd]
        if not cwd:
            out = (fallback, "")
        else:
            root = T.repo_root(cwd) or cwd
            try:
                key = C.normalise_path(root)
            except (OSError, ValueError):
                key = ""
            out = (os.path.basename(root.rstrip("\\/")) or fallback, key)
        self._projects[cwd] = out
        return out


def normalise_record(rec, origin_default: str) -> dict | None:
    if not isinstance(rec, dict):
        return None
    rid = str(rec.get("id") or "").strip()
    text = rec.get("text")
    if not rid or not isinstance(text, str) or not text.strip():
        return None
    if len(text) > RAW_MAX:
        # cut at a whole word: a token cut in half no longer looks like one, so the scrubber would miss it
        cut = text[:RAW_MAX]
        text = cut if text[RAW_MAX].isspace() else re.sub(r"\S+\Z", "", cut)
    out = {"id": rid[:200], "text": text, "ts": iso(rec.get("timestamp")),
           "origin": str(rec.get("origin") or origin_default)[:40]}
    for k in ("project", "root", "session", "role", "ref", "url"):
        v = rec.get(k)
        out[k] = str(v)[:400] if v is not None else ""
    return out


def chunk_text(text: str, size: int = CHUNK, overlap: int = OVERLAP) -> list:
    """About `size` characters each, cut at a space, the next starting `overlap` characters back."""
    text = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n")).strip()
    if len(text) <= size:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = max(text.rfind(" ", start + size // 2, end), text.rfind("\n", start + size // 2, end))
            if cut > start:
                end = cut
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        nxt = max(end - overlap, start + 1)
        sp = text.find(" ", nxt, end)
        start = sp + 1 if 0 <= sp < end - 1 else nxt
    return out


# ---- the lock ----------------------------------------------------------------------------------
class Busy(RuntimeError):
    """Another index run holds the lock."""


class Lock:
    def __init__(self, path: str | None = None):
        self.path = path or _path("index.lock")
        self.held = False

    @staticmethod
    def fresh(path: str | None = None) -> bool:
        """True when a live run holds the lock (touched within LOCK_STALE)."""
        try:
            return time.time() - os.path.getmtime(path or _path("index.lock")) < LOCK_STALE
        except OSError:
            return False

    def acquire(self) -> "Lock":
        C.ensure_dir(os.path.dirname(self.path))
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write("%d %s\n" % (os.getpid(), now_iso()))
                self.held = True
                return self
            except FileExistsError:
                if self.fresh(self.path):
                    raise Busy("an index run is already going (%s)" % self.path) from None
                try:
                    os.unlink(self.path)     # left by a run that died
                except OSError:
                    pass
        raise Busy("could not take the index lock (%s)" % self.path)

    def touch(self) -> None:
        if self.held:
            try:
                os.utime(self.path, None)
            except OSError:
                pass

    def release(self) -> None:
        if self.held:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            self.held = False


# ---- discovery file ----------------------------------------------------------------------------
def search_argv() -> list:
    """The argv that runs a search with this core; the query is appended after "--". Its callers are
    hooks, so the query has a time budget: past it the search prints nothing rather than run on."""
    return [sys.executable or C.python_name(), C.gnomonkit_script(), "history", "search", "--limit", "5",
            "--budget", "%g" % SEARCH_BUDGET_S, "--"]


def write_search_json() -> None:
    C.atomic_write(_path("search.json"), json.dumps(
        {"argv": search_argv(), "core": C.core_version(), "db": db_path(), "written": now_iso(),
         "note": "Gnomonkit core's local history search. Append the query as the last argument."},
        indent=1) + "\n")


# ---- indexing ----------------------------------------------------------------------------------
def last_run() -> dict:
    d = read_json(_path("state.json"), {})
    return d if isinstance(d, dict) else {}


def due(now: float | None = None, cwd: str | None = None) -> bool:
    """True when the session-start refresh should start one: no live run, the last run started more
    than REFRESH_EVERY ago (or never), and indexing is on here (mode(cwd); looked at last, since
    auto may have to look for MemPalace)."""
    if Lock.fresh():
        return False
    started = last_run().get("started_epoch")
    if isinstance(started, (int, float)) and (now or time.time()) - started < REFRESH_EVERY:
        return False
    return not disabled(cwd)


def run_index(args=None, rebuild: bool = False, scrubber=None, exclusions=None, store=None, lock=True) -> dict:
    """Read every enabled source's new records into the index. Returns the run's summary (also saved
    to state.json). Raises Busy when another run holds the lock, and ScrubConfigError (nothing
    indexed) when the extra scrub rules can't be loaded."""
    from . import _scrub
    if disabled():
        return {"disabled": True}
    lk = Lock().acquire() if lock else None
    t0 = time.time()
    summary = {"started": now_iso(t0), "started_epoch": t0, "records": 0, "chunks": 0, "dropped": 0,
               "excluded": 0, "sources": {}, "error": None}
    own_store = store is None
    st = None
    try:
        scrub = scrubber or _scrub.Scrubber.load()
        excluded = exclusions or Exclusions()
        st = store or Store(upgrade=True)
        summary["mode"] = "fts5" if st.fts else "like"
        if rebuild:
            st.con.execute("DELETE FROM chunks")
            st.con.execute("DELETE FROM cursors")
            st.commit()
        for mod in source_modules():
            enabled = getattr(mod, "enabled", None)
            if callable(enabled) and not enabled(args):
                continue
            name = mod.NAME
            counts = {"records": 0, "chunks": 0, "dropped": 0, "excluded": 0}
            ctx = Context(st, name, args, touch=lk.touch if lk else None)
            for raw in mod.records(ctx):
                rec = normalise_record(raw, name)
                if rec is None:
                    continue
                if excluded(rec["project"], rec["root"]):
                    counts["excluded"] += 1
                    continue
                try:
                    clean = scrub(rec["text"])
                    if not isinstance(clean, str):
                        raise TypeError("scrubber returned no text")
                except Exception:  # noqa: BLE001 - fail closed: the record is dropped, never stored raw
                    counts["dropped"] += 1
                    continue
                pieces = chunk_text(clean[:RECORD_MAX])       # cut after scrubbing, never before
                if not pieces:
                    continue
                counts["chunks"] += st.replace_record(name, rec, pieces)
                counts["records"] += 1
                if st.tick() and lk:
                    lk.touch()
            st.commit()
            summary["sources"][name] = counts
            for k in counts:
                summary[k] += counts[k]
        summary["purged"] = purge_excluded(st, excluded)
        summary["pruned"] = prune(st, *retention())
        write_search_json()
    except Exception as e:  # noqa: BLE001 - recorded in state.json, then raised to the command
        summary["error"] = "%s: %s" % (type(e).__name__, e)
        raise
    finally:
        summary["finished"] = now_iso()
        summary["seconds"] = round(time.time() - t0, 2)
        try:
            C.atomic_write(_path("state.json"), json.dumps(summary, indent=1, sort_keys=True) + "\n")
        except OSError:
            pass
        if own_store and st is not None:
            st.close()
        if lk:
            lk.release()
    return summary


def purge_excluded(st: Store, excluded) -> int:
    """Rows of projects excluded since they were indexed. Returns how many went."""
    n = 0
    for project, root in st.con.execute("SELECT DISTINCT project, root FROM chunks").fetchall():
        if excluded(project or "", root or ""):
            n += st.delete("project = ? AND root = ?", (project or "", root or ""))
    return n


def retention() -> tuple:
    """(days, megabytes): memory.history_keep_days and memory.history_max_mb from the personal
    profile, KEEP_DAYS and MAX_MB when they are not set. 0 turns either limit off."""
    out = []
    for key, default in (("memory.history_keep_days", KEEP_DAYS), ("memory.history_max_mb", MAX_MB)):
        v = C.checked_profile_value(key)
        out.append(v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else default)
    return tuple(out)


def _used_bytes(st: Store) -> int:
    page = int(st.con.execute("PRAGMA page_size").fetchone()[0])
    pages = int(st.con.execute("PRAGMA page_count").fetchone()[0])
    free = int(st.con.execute("PRAGMA freelist_count").fetchone()[0])
    return (pages - free) * page


def prune(st: Store, keep_days: int = KEEP_DAYS, max_mb: int = MAX_MB) -> int:
    """Drop rows older than keep_days, then, while the data (the rows and their full-text index)
    takes more than max_mb, the oldest days until it is back under 90% of it. The file does not
    shrink; the next records reuse the freed space, so it stops growing at about the cap. Undated
    rows are not counted against either limit. Returns how many chunks went."""
    n = 0
    if keep_days:
        n += st.delete("ts != '' AND ts < ?", (now_iso(time.time() - keep_days * 86400),))
    if max_mb:
        cap = max_mb * 1024 * 1024
        used = _used_bytes(st)
        if used > cap:
            total = int(st.con.execute("SELECT coalesce(sum(length(text)), 0) FROM chunks").fetchone()[0])
            drop = total - total * (0.9 * cap / used)          # text to remove, in the same proportion
            gone, cutoff = 0, ""
            for day, size in st.con.execute("SELECT substr(ts, 1, 10) d, sum(length(text)) FROM chunks "
                                            "WHERE ts != '' GROUP BY d ORDER BY d").fetchall():
                gone += int(size or 0)
                cutoff = day
                if gone >= drop:
                    break
            if cutoff:
                n += st.delete("ts != '' AND substr(ts, 1, 10) <= ?", (cutoff,))
    return n


# ---- search ------------------------------------------------------------------------------------
class Query:
    def __init__(self, text: str, project: str = "all", since: str | None = None, limit: int = 8,
                 cwd: str | None = None):
        self.text = text or ""
        self.terms = T.terms(self.text)
        self.project = (project or "all").strip()
        self.since = parse_since(since) if since else ""
        self.limit = max(1, min(int(limit or 8), 50))
        here = T.repo_root(cwd or os.getcwd()) or (cwd or os.getcwd())
        try:
            self.here = C.normalise_path(here)
        except (OSError, ValueError):
            self.here = ""

    def filters(self) -> tuple:
        where, params = [], []
        if self.since:
            where.append("c.ts >= ?")
            params.append(self.since)
        if self.project == "here":
            where.append("c.root = ?")
            params.append(self.here)
        elif self.project and self.project != "all":
            where.append("lower(c.project) = ?")
            params.append(self.project.lower())
        return where, params


def parse_since(s: str) -> str:
    """YYYY-MM-DD, or a number of days/weeks back (30d, 2w). ValueError otherwise."""
    s = str(s).strip().lower()
    m = re.fullmatch(r"(\d{1,4})\s*([dw])", s)
    if m:
        days = int(m.group(1)) * (7 if m.group(2) == "w" else 1)
        return now_iso(time.time() - days * 86400)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s.upper() + "T00:00:00Z"
    raise ValueError("--since takes a date (YYYY-MM-DD) or a number of days or weeks back (30d, 2w)")


_COLS = "c.id, c.source, c.record, c.origin, c.session, c.project, c.root, c.role, c.ts, c.ref, c.url, c.text"


def _row(r, score: float) -> dict:
    keys = ("id", "source", "record", "origin", "session", "project", "root", "role", "ts", "ref", "url", "text")
    d = dict(zip(keys, r))
    d["score"] = score
    return d


def _search_fts(st: Store, q: Query) -> list:
    expr = " OR ".join('"%s"' % t.replace('"', '""') for t in q.terms)
    where, params = q.filters()
    sql = ("SELECT %s, bm25(chunks_fts) AS s FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
           "WHERE chunks_fts MATCH ?%s ORDER BY s LIMIT ?" % (_COLS, "".join(" AND " + w for w in where)))
    rows = st.con.execute(sql, [expr] + params + [max(q.limit * 10, 100)]).fetchall()
    return [_row(r[:-1], -float(r[-1])) for r in rows]


def _like(t: str) -> str:
    return "%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _search_like(st: Store, q: Query) -> list:
    """No FTS5: chunks holding any term (LIKE), scored with bm25 over light-stemmed words."""
    where, params = q.filters()
    any_term = "(" + " OR ".join("c.text LIKE ? ESCAPE '\\'" for _ in q.terms) + ")"
    sql = "SELECT %s FROM chunks c WHERE %s%s ORDER BY c.ts DESC LIMIT ?" % (
        _COLS, any_term, "".join(" AND " + w for w in where))
    rows = st.con.execute(sql, [_like(t) for t in q.terms] + params + [LIKE_CANDIDATES]).fetchall()
    total = max(st.count(), 1)
    stems = [T.stem(t) for t in q.terms]
    docs = [[w for w in T.words(r[-1])] for r in rows]
    df = {s: sum(1 for d in docs if any(w.startswith(s) for w in d)) for s in stems}
    avg = (sum(len(d) for d in docs) / len(docs)) if docs else 1.0
    out = []
    for r, d in zip(rows, docs):
        score = 0.0
        for s in stems:
            tf = sum(1 for w in d if w.startswith(s))
            if not tf:
                continue
            idf = math.log((total - df[s] + 0.5) / (df[s] + 0.5) + 1.0)
            score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(d) / (avg or 1.0)))
        if score > 0:
            out.append(_row(r, score))
    return out


class SearchTimeout(RuntimeError):
    """The query ran past its budget and was stopped."""


PROGRESS_EVERY = 2000       # SQLite steps between two looks at the clock, while a budget is set
_clock = time.monotonic


def search(q: Query, store: Store | None = None, scrubber=None, log: bool = True, budget: float = 0) -> dict:
    """{"qid", "mode", "results": [...]}: ranked chunks, one per record, the repo bonus applied,
    scrubbed again. Logs the search (never its words) to queries.jsonl. Opens the index read-only.
    budget (seconds, 0 = none) stops the query when it runs longer: SearchTimeout. A locked or
    unreadable file raises sqlite3.Error; FileNotFoundError when there is no index yet."""
    from . import _scrub
    own = store is None
    st = store or Store(readonly=True)
    try:
        mode = "fts5" if st.fts else "like"
        rows = []
        if q.terms:
            if budget:
                deadline = _clock() + budget
                st.con.set_progress_handler(lambda: 1 if _clock() > deadline else 0, PROGRESS_EVERY)
            try:
                rows = _search_fts(st, q) if st.fts else _search_like(st, q)
            except st.sqlite3.OperationalError as e:
                # an interrupt inside the full-text table's set-up reports itself as a failed constructor
                if budget and ("interrupt" in str(e).lower() or _clock() > deadline):
                    raise SearchTimeout("the search ran past its %g s budget" % budget) from None
                raise
            finally:
                if budget:
                    st.con.set_progress_handler(None, 0)
    finally:
        if own:
            st.close()
    for r in rows:
        if q.here and r["root"] == q.here:
            r["score"] *= REPO_BONUS
            r["here"] = True
    rows.sort(key=lambda r: (-r["score"], -_epoch(r["ts"]) if r["ts"] else 0.0))
    scrub = scrubber or _scrub.Scrubber.load()
    seen, results = set(), []
    for r in rows:
        key = (r["source"], r["record"])
        if key in seen:
            continue
        seen.add(key)
        try:
            r["text"] = scrub(r["text"])
        except Exception:  # noqa: BLE001 - a result that can't be scrubbed is not shown
            continue
        r["snippet"] = snippet(r["text"], q.terms)
        r["rank"] = len(results) + 1
        results.append(r)
        if len(results) >= q.limit:
            break
    qid = "q" + hashlib.sha1(("%s %s %s" % (time.time_ns(), os.getpid(), q.text)).encode("utf-8")).hexdigest()[:10]
    if log:
        try:
            append_jsonl(_path("queries.jsonl"), {"ts": now_iso(), "qid": qid, "results": len(results), "mode": mode,
                                                  "project": q.project if q.project in ("all", "here") else "name"})
        except OSError:
            pass
    return {"qid": qid, "mode": mode, "results": results}


def _epoch(ts: str) -> float:
    try:
        return time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return 0.0


def snippet(text: str, terms: list, size: int = SNIPPET) -> str:
    flat = " ".join(text.split())
    low = flat.lower()
    hits = [low.find(T.stem(t)) for t in terms]
    hits = [h for h in hits if h >= 0]
    start = max(0, min(hits) - size // 4) if hits else 0
    if start:
        sp = flat.find(" ", start)
        start = sp + 1 if 0 <= sp < start + 30 else start
    out = flat[start:start + size]
    return ("... " if start else "") + out + (" ..." if start + size < len(flat) else "")


# ---- marks, forget, status ---------------------------------------------------------------------
def find_query(qid: str) -> dict | None:
    try:
        with open(_path("queries.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                if qid in line:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("qid") == qid:
                        return d
    except OSError:
        pass
    return None


def mark(qid: str, rank: int | None) -> dict:
    """Record which result of a search helped (rank), or that none did (None). ValueError on a bad id."""
    if not re.fullmatch(r"q[0-9a-f]{10}", qid or ""):
        raise ValueError("a query id looks like q0123456789 (each search prints its own)")
    q = find_query(qid)
    if q is None:
        raise ValueError("no search with id %s in this index's log" % qid)
    if rank is not None and not (1 <= rank <= int(q.get("results") or 0)):
        raise ValueError("search %s had %d results; --rank must be 1 to %d" % (
            qid, int(q.get("results") or 0), int(q.get("results") or 0)))
    row = {"ts": now_iso(), "qid": qid, "rank": rank, "used": rank is not None}
    append_jsonl(_path("marks.jsonl"), row)
    return row


def forget(session: str | None = None, project: str | None = None, everything: bool = False,
           store: Store | None = None) -> int:
    """Delete rows. Cursors stay, so what was forgotten is not read back in on the next run
    (`history index --rebuild` reads everything again). The file is vacuumed afterwards."""
    own = store is None
    st = store or Store(create=False)
    try:
        if everything:
            n = st.delete("1 = 1")
        elif session:
            if len(session) >= 8:          # a long enough prefix of a session id names one session
                n = st.delete("session = ? OR session LIKE ? ESCAPE '\\'", (session, _like(session)[1:]))
            else:
                n = st.delete("session = ?", (session,))
        elif project:
            n = st.delete("lower(project) = ?", (project.lower(),))
        else:
            raise ValueError("say what to forget: --session ID, --project NAME or --all")
        st.vacuum()
        return n
    finally:
        if own:
            st.close()


def status() -> dict:
    on, why = mode()
    out = {"db": db_path(), "exists": os.path.exists(db_path()), "disabled": not on, "indexing": why,
           "fts5_here": fts5_available(), "last_run": last_run(), "running": Lock.fresh(),
           "search_json": os.path.exists(_path("search.json"))}
    if out["exists"]:
        try:
            st = Store(readonly=True)
        except FileNotFoundError:
            out["exists"] = False                     # made, but nothing indexed yet
            return out
        except Exception as e:  # noqa: BLE001 - reported, not raised
            out["error"] = str(e)
            return out
        try:
            con = st.con
            out["mode"] = "fts5" if st.fts else "like"
            out["chunks"] = st.count()
            out["records"] = int(con.execute("SELECT count(*) FROM (SELECT DISTINCT source, record FROM chunks)")
                                 .fetchone()[0])
            out["sessions"] = int(con.execute("SELECT count(DISTINCT session) FROM chunks WHERE session != ''")
                                  .fetchone()[0])
            out["projects"] = int(con.execute("SELECT count(DISTINCT project) FROM chunks").fetchone()[0])
            per = dict(con.execute("SELECT source, count(*) FROM cursors GROUP BY source").fetchall())
            out["files"] = int(per.get("sessions", 0))
            out["github_repos"] = int(per.get("work", 0))
            span = con.execute("SELECT min(ts), max(ts) FROM chunks WHERE ts != ''").fetchone()
            out["from"], out["to"] = span[0] or "", span[1] or ""
            out["bytes"] = os.path.getsize(db_path())
        except st.sqlite3.Error as e:
            out["error"] = "can't read it now (%s); try again in a moment" % e
        finally:
            st.close()
    out["keep_days"], out["max_mb"] = retention()
    return out
