"""Orchestration grants: which sessions may start, hand off to, message or stop sessions in which projects.

One file, ~/.gnomonkit/grants.toml (GNOMONKIT_HOME moves the folder), which only the user changes, with
`gnomonkit grants` or `gnomonkit setup security` in a terminal. Default deny: no file, a file that can't be
read or has a problem, an unknown `version`, or no grant that matches means refused.

    version = 1

    [[grant]]
    from = "<projects folder>/*"        # where the requesting session runs
    to = ["<projects folder>/*"]        # where it may act; "same-project" = its own project; "*" = anywhere
    except = ["<projects folder>/vault"]   # optional: never these, whatever `to` says
    actions = ["spawn", "handoff", "message", "stop"]
    agents = ["claude"]

Paths are absolute and compared normalised: `~` expanded, `..` and symlinks resolved, one separator,
case-folded on Windows. `<dir>/*` is any folder directly under <dir>, `<dir>/**` any folder at any depth
under it, a plain path that folder only. A session's folder is its project: its repository root (the main
checkout's top level, so a linked worktree counts as its repository; else the directory, with
`<project>/.claude/worktrees/<name>` and `<project>.worktrees/<name>` read as <project>), on both sides of
a grant.

Inside one project a spawn grant covers more (implied_by_spawn()): a handoff into the same project, stopping
the asking session itself, and messaging or stopping a session dispatch started for the asking session.
Across projects, and for any other session, each action takes its own grant.

Who reads this: session-dispatch (spawn, handoff, stop) and the interjector's say, block and hold
(message) call authorize() before acting; so does the core guard hook, for the `claude` command lines an
agent runs itself (`claude --bg`, `claude stop|kill|rm|attach|respawn`) and for SendMessage to
another session; the core `gnomonkit grants` command shows and changes the file.

The grants apply unless the caller runs where only a person can be: CLAUDECODE absent and stdin and stdout
both a real terminal (in_claude_code()). An agent's tool shell has no terminal, so clearing CLAUDECODE
doesn't turn the checks off.
Each plugin installs on its own, so two byte-identical copies of this module ship, in
plugins/core/hooks/ and plugins/interjector/hooks/; a test fails when they differ.

The audit log is here too, for every caller: audit_append() writes one JSON line per event, append-only,
the file 0600. Orchestration decisions go to ~/.gnomonkit/dispatch/audit.jsonl (audit_path()); another
area passes its own name (audit_path("trust")). Never a brief or a message's text.

None of this is a sandbox. An agent with an unrestricted shell can start `claude` itself or reach the file
by means no check foresees. The checks stop the plain cases, the core guard hook keeps agents from editing
the file, and the log shows what happened.

Standard library only, no work at import time.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time

VERSION = 1
ACTIONS = ("spawn", "handoff", "message", "stop")
AGENTS_SUPPORTED = ("claude",)       # other names are accepted in the file and ignored until supported
SAME_PROJECT = "same-project"
ANYWHERE = "*"
GRANT_KEYS = ("from", "to", "except", "actions", "agents")
GIT_TIMEOUT_S = 5
LIST_TIMEOUT_S = 15                  # `claude agents --json --all`, inside a hook's time limit
FIELD_MAX = 400                      # audit values are capped: the log records decisions, not payloads
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_AGENT_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}$")


# ---- where things live -------------------------------------------------------------------------
def gnomonkit_home() -> str:
    raw = (os.environ.get("GNOMONKIT_HOME") or "").strip()
    return os.path.abspath(os.path.expanduser(raw)) if raw else os.path.join(os.path.expanduser("~"), ".gnomonkit")


def grants_path() -> str:
    return os.path.join(gnomonkit_home(), "grants.toml")


def audit_path(area: str = "dispatch") -> str:
    return os.path.join(gnomonkit_home(), re.sub(r"[^A-Za-z0-9_-]", "", area) or "dispatch", "audit.jsonl")


def _console(stream) -> bool:
    """True when `stream` is a real terminal. On Windows isatty() is also true of the NUL device, so a
    console is asked for its mode as well, which NUL has none of."""
    try:
        if stream is None or not stream.isatty():
            return False
        if os.name != "nt":
            return True
        import ctypes
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint32()
        return bool(ctypes.windll.kernel32.GetConsoleMode(ctypes.c_void_p(handle), ctypes.byref(mode)))
    except Exception:  # noqa: BLE001 - anything odd is not a terminal
        return False


def at_terminal(stdin=None, stdout=None) -> bool:
    """A person at a terminal: stdin and stdout both a real terminal (an agent's tool shell has neither)."""
    return _console(sys.stdin if stdin is None else stdin) and _console(sys.stdout if stdout is None else stdout)


def in_claude_code() -> bool:
    """Whether the grants apply: true unless this runs where only a person can be, which takes both
    CLAUDECODE absent (any value, even empty, means a Claude Code shell) and stdin and stdout a real
    terminal. Clearing CLAUDECODE from an agent's shell doesn't give it a terminal, so the checks still run.
    A person needs no grant from themselves; one who pipes the output gets checked like an agent."""
    return "CLAUDECODE" in os.environ or not at_terminal()


# ---- paths -------------------------------------------------------------------------------------
def resolve(p) -> str:
    """For storing and showing: absolute, ~ expanded, `..` and symlinks resolved, forward slashes."""
    p = os.path.realpath(os.path.abspath(os.path.expanduser(str(p).strip())))
    p = p.replace("\\", "/")
    if len(p) > 1 and p.endswith("/") and not re.match(r"^[A-Za-z]:/$", p):
        p = p.rstrip("/")
    return p


def norm(p) -> str:
    """For comparing: resolve(), then case-folded on Windows (its file system ignores case)."""
    p = resolve(p)
    return p.casefold() if os.name == "nt" else p


def _parent(p: str) -> str:
    head = p.rsplit("/", 1)[0] if "/" in p else p
    if head == "":
        return "/"
    if re.match(r"^[A-Za-z]:$", head):
        return head + "/"
    return head


def _under(p: str, base: str) -> bool:
    return p != base and p.startswith(base.rstrip("/") + "/")


_WORKTREE_PATH = re.compile(r"^(.+?)/\.claude/worktrees/[^/]+(?:/.*)?$|^(.+?)\.worktrees/[^/]+(?:/.*)?$", re.I)


def worktree_home(p: str) -> str:
    """The project a worktree path belongs to, by its form alone: `<project>/.claude/worktrees/<name>` and
    `<project>.worktrees/<name>` (and anything under them) are `<project>`; any other path is itself."""
    m = _WORKTREE_PATH.match(p)
    return (m.group(1) or m.group(2)) if m else p


def repo_root(folder) -> str:
    """The project `folder` belongs to, resolved for showing: the main checkout's top level, so a linked
    worktree counts as the repository it was made from; else the git top level; without git, the folder,
    with a worktree path (`<project>/.claude/worktrees/<name>`, `<project>.worktrees/<name>`) read as its
    project."""
    folder = str(folder)
    try:
        r = subprocess.run(["git", "-C", folder, "rev-parse", "--path-format=absolute", "--show-toplevel",
                            "--git-common-dir"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_S, creationflags=NO_WINDOW)
        lines = [x.strip() for x in (r.stdout or "").splitlines()] if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        lines = []
    top = lines[0] if lines else ""
    common = lines[1] if len(lines) > 1 else ""
    if top and common and os.path.basename(common.rstrip("/\\")).lower() == ".git":
        main = os.path.dirname(common.rstrip("/\\"))
        if main and norm(main) != norm(top) and os.path.isdir(main):
            return resolve(main)
    if top:
        return resolve(top)
    return worktree_home(resolve(folder))


def session_entry(entries, session_id: str):
    """The `claude agents --json --all` entry for a session: exact id or sessionId, else a unique sessionId
    prefix of at least 8 characters (what `dispatch.py list` shows). None when there is none."""
    sid = str(session_id or "").strip()
    if not sid or not entries:
        return None
    rows = [e for e in entries if isinstance(e, dict)]
    for e in rows:
        if sid in (str(e.get("id") or ""), str(e.get("sessionId") or "")):
            return e
    if len(sid) >= 8:
        hits = [e for e in rows if str(e.get("sessionId") or "").startswith(sid)]
        if len(hits) == 1:
            return hits[0]
    return None


def session_folder(entries, session_id: str) -> str | None:
    e = session_entry(entries, session_id)
    cwd = e.get("cwd") if e else None
    return cwd if isinstance(cwd, str) and cwd.strip() else None


def _key(e: dict) -> str:
    return str(e.get("sessionId") or e.get("id") or "")


def find_requester(entries=None, session_id=None, pid=None, fallback=None) -> tuple:
    """(folder, problem) for the requesting session: its own folder as the session list reports it, found
    by its session id (CLAUDE_CODE_SESSION_ID unless given) and by its process (CLAUDE_PID), so a `cd` in its
    shell doesn't change it. When both are found and name different sessions, the folder is None and
    `problem` says why: one of the two was borrowed. When neither is listed, `fallback`, else the current
    folder."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID") if session_id is None else session_id
    pid = str((os.environ.get("CLAUDE_PID") if pid is None else pid) or "").strip()
    by_sid = session_entry(entries, sid or "")
    by_pid = None
    if pid.isdigit() and entries:
        hits = [e for e in entries if isinstance(e, dict) and str(e.get("pid")) == pid]
        by_pid = hits[0] if len(hits) == 1 else None
    if by_sid is not None and by_pid is not None and _key(by_sid) != _key(by_pid):
        return None, "the session id and the session's process name different sessions"
    e = by_sid if by_sid is not None else by_pid
    cwd = e.get("cwd") if e else None
    if isinstance(cwd, str) and cwd.strip() and os.path.isdir(cwd):
        return cwd, None
    return (fallback or os.getcwd()), None


def requester_dir(entries=None) -> str:
    """find_requester()'s folder, the current folder when it has none."""
    return find_requester(entries)[0] or os.getcwd()


def claude_command():
    """The claude CLI as a command list: GNOMONKIT_CLAUDE_CMD (a JSON list, for tests and odd installs), else
    `claude` on PATH. None when there is none."""
    raw = os.environ.get("GNOMONKIT_CLAUDE_CMD")
    if raw:
        try:
            cmd = json.loads(raw)
        except ValueError:
            return None
        return [str(c) for c in cmd] if isinstance(cmd, list) and cmd else None
    exe = shutil.which("claude")
    return [exe] if exe else None


def fetch_sessions(timeout: float = LIST_TIMEOUT_S):
    """The `claude agents --json --all` listing, a list of dicts; None when it can't be had."""
    cmd = claude_command()
    if not cmd:
        return None
    try:
        r = subprocess.run([*cmd, "agents", "--json", "--all"], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=NO_WINDOW)
        rows = json.loads(r.stdout or "[]") if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return [e for e in rows if isinstance(e, dict)] if isinstance(rows, list) else None


def target_entries(entries, ref, partial: bool = False) -> list:
    """Every listing entry `ref` could name: an id or sessionId, a unique sessionId prefix of 8 or more, a
    name (any case), `name [ref]`; with `partial` (what `claude attach` accepts) also any entry whose name
    contains it or whose id starts with it. A caller that must be sure checks them all."""
    ref = str(ref or "").strip()
    rows = [e for e in (entries or []) if isinstance(e, dict)]
    if not ref or not rows:
        return []
    out = []
    m = re.match(r"^(.*?)\s*\[([^\[\]]+)\]$", ref)
    refs = [ref] + ([m.group(1).strip(), m.group(2).strip()] if m else [])
    for r in [x for x in refs if x]:
        e = session_entry(rows, r)
        if e is not None:
            out.append(e)
        low = r.casefold()
        for e in rows:
            name = str(e.get("name") or "").casefold()
            ids = [str(e.get(k) or "").casefold() for k in ("id", "sessionId")]
            if name == low or (partial and ((low in name) or any(i and i.startswith(low) for i in ids))):
                out.append(e)
    seen, uniq = set(), []
    for e in out:
        if id(e) not in seen:
            seen.add(id(e))
            uniq.append(e)
    return uniq


# ---- patterns ----------------------------------------------------------------------------------
class Pattern:
    """One `from`, `to` or `except` entry. kind: exact | children (/*) | descendants (/**) | anywhere (*) |
    same (same-project)."""

    __slots__ = ("raw", "kind", "base")

    def __init__(self, raw: str, kind: str, base: str = ""):
        self.raw, self.kind, self.base = raw, kind, base

    def matches(self, target: str, origin: str = "") -> bool:
        """`target` and `origin` are norm()ed folders."""
        if self.kind == "anywhere":
            return True
        if self.kind == "same":
            return bool(origin) and target == origin
        if self.kind == "exact":
            return target == self.base
        if self.kind == "children":
            return target != self.base and _parent(target) == self.base
        return _under(target, self.base)


def parse_pattern(raw, field: str) -> Pattern:
    """ValueError says what is wrong. `*` and `same-project` are valid only in `to`."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("%s entries must be folder paths (text)" % field)
    s = raw.strip()
    if s in (ANYWHERE, SAME_PROJECT):
        if field != "to":
            raise ValueError("%r is allowed only in `to`" % s)
        return Pattern(s, "anywhere" if s == ANYWHERE else "same")
    t = s.replace("\\", "/")
    kind = "exact"
    if t.endswith("/**"):
        kind, t = "descendants", t[:-3]
    elif t.endswith("/*"):
        kind, t = "children", t[:-2]
    t = t or "/"
    if re.match(r"^[A-Za-z]:$", t):
        t += "/"
    if any(ch in t for ch in "*?["):
        raise ValueError("%s: %r: a wildcard may only end a path, as /* or /**" % (field, s))
    if not os.path.isabs(os.path.expanduser(t)):
        raise ValueError("%s: %r is not an absolute path" % (field, s))
    return Pattern(s, kind, norm(t))


def pattern_text(folder, kind: str = "exact") -> str:
    """A folder as a pattern for the file: resolved, forward slashes, with /* or /** for those kinds."""
    base = resolve(folder)
    if kind == "children":
        return base.rstrip("/") + "/*"
    if kind == "descendants":
        return base.rstrip("/") + "/**"
    return base


# ---- TOML in -----------------------------------------------------------------------------------
def parse_toml(text: str) -> dict:
    """tomllib on Python 3.11+. Older Pythons get a small reader for the layout `gnomonkit grants` writes
    (tables, arrays of tables, strings, integers, booleans, arrays); anything else is a ValueError, which
    a caller treats as a malformed file: refused."""
    try:
        import tomllib
    except ModuleNotFoundError:
        return _small_toml(text)
    return tomllib.loads(text)


_ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}


def _gap(s: str, i: int) -> int:
    """Past spaces, newlines and comments (inside an array)."""
    while i < len(s) and (s[i] in " \t\r\n" or s[i] == "#"):
        if s[i] == "#":
            while i < len(s) and s[i] != "\n":
                i += 1
        else:
            i += 1
    return i


def _scalar(s: str, i: int):
    while i < len(s) and s[i] in " \t":
        i += 1
    if i >= len(s) or s[i] in "\r\n":
        raise ValueError("missing value")
    ch = s[i]
    if ch in "\"'":
        if s.startswith(ch * 3, i):
            raise ValueError("multi-line strings need Python 3.11+")
        out, i = [], i + 1
        while i < len(s) and s[i] != ch:
            if s[i] == "\n":
                raise ValueError("unterminated string")
            if ch == '"' and s[i] == "\\":
                nxt = s[i + 1:i + 2]
                if nxt in _ESC:
                    out.append(_ESC[nxt])
                    i += 2
                    continue
                if nxt in ("u", "U"):
                    n = 4 if nxt == "u" else 8
                    out.append(chr(int(s[i + 2:i + 2 + n], 16)))
                    i += 2 + n
                    continue
                raise ValueError("bad escape")
            out.append(s[i])
            i += 1
        if i >= len(s):
            raise ValueError("unterminated string")
        return "".join(out), i + 1
    if ch == "[":
        items, i = [], i + 1
        while True:
            i = _gap(s, i)
            if i < len(s) and s[i] == "]":
                return items, i + 1
            v, i = _scalar(s, i)
            items.append(v)
            i = _gap(s, i)
            if i < len(s) and s[i] == ",":
                i += 1
            elif i >= len(s) or s[i] != "]":
                raise ValueError("bad or unterminated array")
    m = re.match(r"[A-Za-z0-9_+.-]+", s[i:])
    if not m:
        raise ValueError("bad value")
    tok = m.group(0)
    if tok in ("true", "false"):
        return tok == "true", i + m.end()
    if re.match(r"[+-]?\d[\d_]*$", tok):
        return int(tok.replace("_", "")), i + m.end()
    raise ValueError("unsupported value %r" % tok[:20])


def _small_toml(text: str) -> dict:
    data: dict = {}
    cur = data
    body = text.replace("\r\n", "\n")
    i = 0
    while i < len(body):
        nl = body.find("\n", i)
        line = body[i:nl if nl >= 0 else len(body)].strip()
        start, i = i, (nl + 1 if nl >= 0 else len(body))
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[\[\s*([A-Za-z0-9_-]+)\s*\]\]\s*(#.*)?$", line) or None
        if m:
            lst = data.setdefault(m.group(1), [])
            if not isinstance(lst, list):
                raise ValueError("%s is not an array of tables" % m.group(1))
            cur = {}
            lst.append(cur)
            continue
        m = re.match(r"^\[\s*([A-Za-z0-9_-]+)\s*\]\s*(#.*)?$", line)
        if m:
            cur = data.setdefault(m.group(1), {})
            if not isinstance(cur, dict):
                raise ValueError("%s is not a table" % m.group(1))
            continue
        m = re.match(r'^\s*([A-Za-z0-9_-]+|"[^"\\\n]*")\s*=', body[start:])
        if not m:
            raise ValueError("a line is not key = value")
        key = m.group(1).strip('"')
        value, end = _scalar(body, start + m.end())
        rest = body[end:body.find("\n", end) if body.find("\n", end) >= 0 else len(body)].strip()
        if rest and not rest.startswith("#"):
            raise ValueError("unexpected text after the value of %s" % key)
        if key in cur:
            raise ValueError("%s is set twice" % key)
        cur[key] = value
        nl = body.find("\n", end)
        i = nl + 1 if nl >= 0 else len(body)
    return data


# ---- the file ----------------------------------------------------------------------------------
class Grant:
    __slots__ = ("index", "source", "from_", "to", "except_", "actions", "agents")

    def __init__(self, index, source, from_, to, except_, actions, agents):
        self.index, self.source = index, source
        self.from_, self.to, self.except_, self.actions, self.agents = from_, to, except_, actions, agents

    def describe(self) -> str:
        to = ", ".join(p.raw for p in self.to)
        out = "from %s to %s" % (self.from_.raw, to)
        if self.except_:
            out += " except %s" % ", ".join(p.raw for p in self.except_)
        return out + ": %s (%s)" % (", ".join(self.actions), ", ".join(self.agents))


class Loaded:
    """The grants file, checked. status: ok | missing | unreadable | malformed | version. Only `ok` allows
    anything. `data` is the parsed file (for rewriting; [[trust]] tables stay there), `problems` one line
    each."""

    __slots__ = ("path", "status", "problems", "grants", "data")

    def __init__(self, path, status, problems=(), grants=(), data=None):
        self.path, self.status = path, status
        self.problems, self.grants, self.data = list(problems), list(grants), data

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _str_list(v, field: str, n: int, problems: list) -> list:
    if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v):
        problems.append("grant %d: %s must be a non-empty list of text" % (n, field))
        return []
    return [x.strip() for x in v]


def check(data) -> tuple:
    """(status, problems, grants) for a parsed file."""
    if not isinstance(data, dict):
        return "malformed", ["the file is not a TOML document"], []
    version = data.get("version")
    if "version" not in data:
        return "malformed", ["`version = %d` is missing" % VERSION], []
    if type(version) is not int or version != VERSION:
        return "version", ["version %r is not one this Gnomonkit reads (it reads %d)" % (version, VERSION)], []
    raw = data.get("grant", [])
    if not isinstance(raw, list) or not all(isinstance(g, dict) for g in raw):
        return "malformed", ["`grant` must be [[grant]] tables"], []
    problems: list = []
    grants: list = []
    for n, g in enumerate(raw, 1):
        before = len(problems)
        unknown = sorted(k for k in g if k not in GRANT_KEYS)
        if unknown:
            problems.append("grant %d: unknown key %s (keys: %s)" % (n, ", ".join(unknown), ", ".join(GRANT_KEYS)))
        pats: dict = {}
        for field in ("from", "to", "except"):
            if field == "from":
                if not isinstance(g.get("from"), str):
                    problems.append("grant %d: from must be one folder path (text)" % n)
                    continue
                items = [g["from"]]
            elif field == "except" and "except" not in g:
                pats["except"] = []
                continue
            else:
                items = _str_list(g.get(field), field, n, problems)
            out = []
            for item in items:
                try:
                    out.append(parse_pattern(item, field))
                except ValueError as e:
                    problems.append("grant %d: %s" % (n, e))
            pats[field] = out
        actions = _str_list(g.get("actions"), "actions", n, problems)
        bad = [a for a in actions if a not in ACTIONS]
        if bad:
            problems.append("grant %d: unknown action %s (actions: %s)" % (n, ", ".join(bad), ", ".join(ACTIONS)))
        agents = _str_list(g.get("agents"), "agents", n, problems)
        bad = [a for a in agents if not _AGENT_NAME.match(a)]
        if bad:
            problems.append("grant %d: agent names are lower-case words, not %s" % (n, ", ".join(bad)))
        if len(problems) == before:
            grants.append(Grant(n, g, pats["from"][0], pats["to"], pats["except"], actions, agents))
    if problems:
        return "malformed", problems, []
    return "ok", [], grants


def load(path: str | None = None) -> Loaded:
    """Never raises: every failure is a status that refuses."""
    path = path or grants_path()
    try:
        with open(path, encoding="utf-8-sig") as fh:    # a BOM (Notepad, PowerShell) is no error
            text = fh.read()
    except FileNotFoundError:
        return Loaded(path, "missing", ["there is no grants file yet"])
    except (OSError, UnicodeDecodeError) as e:
        return Loaded(path, "unreadable", ["the file can't be read (%s)" % type(e).__name__])
    try:
        data = parse_toml(text)
    except Exception as e:  # noqa: BLE001 - any parse failure refuses
        return Loaded(path, "malformed", ["the file is not valid TOML (%s)" % str(e)[:200]])
    try:
        status, problems, grants = check(data)
    except Exception as e:  # noqa: BLE001 - a checker bug refuses too
        return Loaded(path, "malformed", ["the file could not be checked (%s)" % type(e).__name__], data=data)
    return Loaded(path, status, problems, grants, data)


# ---- decisions ---------------------------------------------------------------------------------
class Decision:
    """allowed, and why. decision: allow | deny | unchecked (run outside Claude Code). grant: the 1-based
    number of the grant that allowed it."""

    __slots__ = ("decision", "action", "agent", "from_dir", "to_dir", "grant", "reason", "status", "path")

    def __init__(self, decision, action, agent, from_dir, to_dir, grant=None, reason="", status="", path=""):
        self.decision, self.action, self.agent = decision, action, agent
        self.from_dir, self.to_dir, self.grant = from_dir, to_dir, grant
        self.reason, self.status, self.path = reason, status, path

    @property
    def allowed(self) -> bool:
        return self.decision != "deny"

    def as_dict(self) -> dict:
        return {"decision": self.decision, "action": self.action, "agent": self.agent, "from": self.from_dir,
                "to": self.to_dir, "grant": self.grant, "reason": self.reason, "grants_file": self.path}


def decide(from_dir, to_dir, action: str, agent: str = "claude", loaded: Loaded | None = None) -> Decision:
    """May a session whose project is `from_dir` do `action` in `to_dir`? Both are folders (repository roots
    already, or the caller doesn't care). Default deny."""
    loaded = loaded if loaded is not None else load()
    f = resolve(from_dir) if from_dir else ""
    t = resolve(to_dir) if to_dir else ""

    def deny(reason, status):
        return Decision("deny", action, agent, f, t, None, reason, status, loaded.path)

    if not loaded.ok:
        return deny("; ".join(loaded.problems) or loaded.status, loaded.status)
    if action not in ACTIONS:
        return deny("%r is not an action (actions: %s)" % (action, ", ".join(ACTIONS)), "action")
    if agent not in AGENTS_SUPPORTED:
        return deny("agent %r is not supported yet" % agent, "agent")
    if not t:
        return deny("the folder of the session to act on is not known", "target")
    nf, nt = norm(f), norm(t)
    for g in loaded.grants:
        if agent in g.agents and action in g.actions and g.from_.matches(nf) \
                and any(p.matches(nt, nf) for p in g.to) and not any(p.matches(nt, nf) for p in g.except_):
            return Decision("allow", action, agent, f, t, g.index, "grant %d" % g.index, "ok", loaded.path)
    return deny("no grant allows it", "no-grant")


# ---- what the spawn grant implies inside one project ---------------------------------------------------
def roles_store() -> dict:
    """session-dispatch's record of the sessions it started ($GNOMONKIT_HOME/dispatch/roles.json), read
    only; {} when there is none or it can't be read."""
    try:
        with open(os.path.join(gnomonkit_home(), "dispatch", "roles.json"), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _record_of(store: dict, ids) -> dict | None:
    ids = [i for i in ids if i]
    for i in ids:
        if i in store:
            return store[i]
    for rec in store.values():
        if rec.get("sessionId") and rec.get("sessionId") in ids:
            return rec
    return None


def implied_by_spawn(action: str, target, entries=None, requester_id: str | None = None,
                     store: dict | None = None) -> str | None:
    """Why the spawn grant covers `action` too, inside one project (the caller has checked both sides are the
    same project), else None: a handoff into the same project; stopping the requesting session itself (a
    handoff retiring its own session); messaging or stopping a session dispatch started for this session
    (its record's parent_id is the requester's session id). Anything else needs its own grant."""
    if action == "handoff":
        return "a handoff inside the same project"
    if action not in ("message", "stop"):
        return None
    me = str(requester_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if not me or not target:
        return None
    e = target if isinstance(target, dict) else session_entry(entries, str(target))
    ids = [str(e.get(k) or "") for k in ("id", "sessionId")] if isinstance(e, dict) else [str(target)]
    if me in ids:
        return "this session itself" if action == "stop" else None
    rec = _record_of(roles_store() if store is None else store, ids)
    if rec and str(rec.get("parent_id") or "") == me:
        return "a session this session started"
    return None


def suggestion_toml(d: Decision) -> str:
    return "\n".join(["[[grant]]", 'from = "%s"' % d.from_dir, 'to = ["%s"]' % (d.to_dir or "<folder>"),
                      'actions = ["%s"]' % d.action, 'agents = ["%s"]' % d.agent])


def add_command(d: Decision, prog: str = "gnomonkit") -> str:
    return '%s grants add --from "%s" --to "%s" --actions %s' % (prog, d.from_dir, d.to_dir or "<folder>", d.action)


_WHAT = {"spawn": "start a session", "handoff": "hand off to a fresh session", "message": "message the session",
         "stop": "stop the session"}


def core_script() -> str | None:
    """Core's gnomonkit.py: beside this file's plugin (core's copy), core beside interjector (a checkout),
    else core's user-scope install in Claude Code's record of installed plugins. None when not found."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (os.path.join(here, "gnomonkit.py"), os.path.join(os.path.dirname(here), "core", "gnomonkit.py")):
        if os.path.isfile(p):
            return p
    cfg = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip() or os.path.join(os.path.expanduser("~"), ".claude")
    try:
        with open(os.path.join(cfg, "plugins", "installed_plugins.json"), encoding="utf-8") as fh:
            plugins = json.load(fh).get("plugins") or {}
        keys = sorted((k for k in plugins if k.split("@", 1)[0] == "core"), key=lambda k: (k != "core@gnomonkit", k))
    except (OSError, ValueError, AttributeError):
        return None
    for key in keys:
        for entry in plugins.get(key) if isinstance(plugins.get(key), list) else []:
            if isinstance(entry, dict) and entry.get("scope") == "user" and isinstance(entry.get("installPath"), str):
                p = os.path.join(entry["installPath"], "gnomonkit.py")
                if os.path.isfile(p):
                    return p
    return None


def gnomonkit_prog() -> str:
    """How the user runs core's command in a terminal: `python "<core>/gnomonkit.py"`, which works when
    `gnomonkit` is not on PATH; plain `gnomonkit` only when core's script can't be found."""
    script = core_script()
    return 'python "%s"' % script if script else "gnomonkit"


def refusal_text(d: Decision, prog: str | None = None) -> str:
    """What a refused caller prints: what was refused and why, the grant that would allow it, and the
    command the user runs to add it. The agent can't run that command; it gives it to the user."""
    prog = prog or gnomonkit_prog()
    head = "refused by orchestration grants: a session in %s may not %s in %s (%s)." % (
        d.from_dir or "?", _WHAT.get(d.action, d.action), d.to_dir or "an unknown folder", d.reason)
    if d.status in ("malformed", "unreadable", "version"):
        return (head + "\nNothing is allowed until the user fixes %s (`%s grants list` shows the problems)."
                % (d.path, prog))
    if d.status in ("action", "agent", "target", "requester"):
        return head
    lines = [head, "Only the user can allow it. The grant that would, in %s:" % d.path, ""]
    lines += ["    " + ln for ln in suggestion_toml(d).splitlines()]
    lines += ["", "The user adds it by running this in a terminal (it refuses to run for an agent):",
              "    " + add_command(d, prog)]
    if d.status == "missing":
        lines.append("or sets grants up with `%s setup security`." % prog)
    return "\n".join(lines)


def _told_path(requester_id: str) -> str:
    return os.path.join(gnomonkit_home(), "dispatch", "told", re.sub(r"[^A-Za-z0-9_-]", "", requester_id)[:80]
                        + ".json")


def refusal_once(d: Decision, requester_id: str | None = None, prog: str | None = None) -> str:
    """refusal_text() the first time a session meets this missing grant; after that, one line saying it was
    named already, so a session that keeps trying doesn't hand the user the same command again and again.
    Remembered in $GNOMONKIT_HOME/dispatch/told/<session>.json. Without a session id, the full text."""
    full = refusal_text(d, prog)
    me = str(requester_id or os.environ.get("CLAUDE_CODE_SESSION_ID") or "").strip()
    if not me or d.status not in ("no-grant", "missing"):
        return full
    key = "%s|%s|%s" % (d.action, norm(d.from_dir) if d.from_dir else "", norm(d.to_dir) if d.to_dir else "")
    path = _told_path(me)
    try:
        with open(path, encoding="utf-8") as fh:
            told = json.load(fh)
        told = told if isinstance(told, list) else []
    except (OSError, ValueError):
        told = []
    if key in told:
        return ("refused by orchestration grants: a session in %s may not %s in %s. The missing grant was named "
                "earlier in this session, with the command that adds it; don't ask the user again until they have "
                "added it." % (d.from_dir or "?", _WHAT.get(d.action, d.action), d.to_dir or "an unknown folder"))
    try:
        _private_dir(os.path.dirname(path))
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump((told + [key])[-200:], fh)
        os.replace(tmp, path)
    except OSError:
        pass
    return full


def authorize(action: str, to_dir, entries=None, session: str | None = None, agent: str = "claude",
              log: bool = True, loaded: Loaded | None = None, requester=None, checked: bool | None = None,
              via: str | None = None, target=None, requester_id: str | None = None) -> Decision:
    """The check dispatch, the interjector and the guard hook run before acting. When the grants apply
    (in_claude_code(), or `checked`): decide() between the requesting session's repository root and
    `to_dir`'s (None when the target's folder is unknown, which refuses). Otherwise (a person at a terminal):
    `unchecked`. `requester` is find_requester()'s (folder, problem), worked out here when not given; a
    problem refuses. Inside one project, a spawn grant also allows what implied_by_spawn() says it covers;
    `target` is the session acted on (an id or a listing entry) and `requester_id` the asking session's id
    (default CLAUDE_CODE_SESSION_ID). Every decision goes to the audit log unless `log` is false (dry runs);
    `via` names the caller there."""
    folder, problem = requester if requester is not None else find_requester(entries)
    from_dir = repo_root(folder) if folder else ""
    to_root = repo_root(to_dir) if to_dir else ""
    if checked or (checked is None and in_claude_code()):
        if problem:
            d = Decision("deny", action, agent, from_dir, to_root, None, problem, "requester",
                         (loaded.path if loaded is not None else grants_path()))
        else:
            loaded = loaded if loaded is not None else load()
            d = decide(from_dir, to_root, action, agent, loaded)
            if d.status == "no-grant" and from_dir and to_root and norm(from_dir) == norm(to_root):
                why = implied_by_spawn(action, target, entries, requester_id)
                spawn = decide(from_dir, to_root, "spawn", agent, loaded) if why else None
                if spawn is not None and spawn.decision == "allow":
                    d = Decision("allow", action, agent, from_dir, to_root, spawn.grant,
                                 "grant %d (spawn; %s)" % (spawn.grant, why), "ok", loaded.path)
    else:
        d = Decision("unchecked", action, agent, from_dir, to_root, None,
                     "run outside a Claude Code session: no grant needed", "unchecked", grants_path())
    if log:
        audit_decision(d, session, via)
    return d


# ---- the audit log -----------------------------------------------------------------------------
def _cap(v):
    if isinstance(v, str):
        return v if len(v) <= FIELD_MAX else v[:FIELD_MAX] + "..."
    if isinstance(v, (list, tuple)):
        return [_cap(x) for x in list(v)[:50]]
    if isinstance(v, dict):
        return {str(k)[:60]: _cap(x) for k, x in list(v.items())[:50]}
    if v is None or isinstance(v, (bool, int, float)):
        return v
    return _cap(str(v))


def _private_dir(folder: str) -> None:
    missing, p = [], os.path.abspath(folder)
    while not os.path.isdir(p):
        missing.append(p)
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    for d in reversed(missing):
        try:
            os.mkdir(d, 0o700)
        except FileExistsError:
            pass
        if os.name != "nt":
            os.chmod(d, 0o700)


def audit_append(record: dict, path: str | None = None) -> bool:
    """Append one event to an audit log as one JSON line: never rewritten, the file 0600 and new folders
    0700 on POSIX, a symlink at the path refused where the OS can tell. "time" (UTC) is added when absent;
    values are capped at FIELD_MAX characters. Never raises: False when nothing was written."""
    try:
        path = path or audit_path()
        _private_dir(os.path.dirname(path))
        rec = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        rec.update({str(k): _cap(v) for k, v in dict(record).items()})
        line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            view = memoryview(line)
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)
        return True
    except Exception:  # noqa: BLE001 - logging must never take the caller down
        return False


def audit_decision(d: Decision, session: str | None = None, via: str | None = None) -> bool:
    rec = {"event": "decision", "action": d.action, "agent": d.agent, "from": d.from_dir, "to": d.to_dir,
           "decision": d.decision, "grant": d.grant, "session": session,
           "requester": os.environ.get("CLAUDE_CODE_SESSION_ID") or None, "reason": d.reason}
    if via:
        rec["via"] = via
    return audit_append(rec)
