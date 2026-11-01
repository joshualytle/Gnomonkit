"""Shared helpers for the `gnomonkit` command and for the core hooks that read the personal profile.

Standard library only and cheap to import: SessionStart and UserPromptSubmit hooks load this module,
so nothing here starts a process or imports a large module at import time.

    gnomonkit_home()                         ~/.gnomonkit, or GNOMONKIT_HOME
    parse_toml, read_toml, load_toml         TOML in (tomllib on 3.11+, a small fallback parser below)
    dumps(data, layout, header)              TOML out, in a fixed layout (the standard library has no writer)
    SCHEMA, SCHEMA_BY_KEY, coerce, check_value, validate_profile   the personal profile's one schema
    personal_profile_path, load_personal, profile_value, setting   where the profile is and what it says
    resolve_path, normalise_path, path_within                      paths for storing and for comparing
    require_tty(word), is_terminal()         the confirmation that only a person at a terminal can give
    atomic_write, ensure_dir                 same-folder temp file + os.replace; 0600 files, 0700 folders
    command_modules()                        the subcommand modules gnomonkit.py registers
    machine_kind(), is_personal_machine()    personal | work | unknown (treated as work), from machine.toml
    mempalace_status(cwd, refresh)           {"installed", "mcp", "where"}: MemPalace set up here? cached 1 h
    memory_feature(key, env, cwd)            (on, why) for memory.history / memory.lessons: on, off or auto

Precedence for a setting that is also a plugin option (context_budget, max_compactions, scratch_root,
corrections_repo): the plugin option (CLAUDE_PLUGIN_OPTION_<NAME>, then GNOMONKIT_<NAME>), then the
personal profile, then the default.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

# ---- where things live -------------------------------------------------------------------------
DISCLAIMER_VERSION = "1"   # bump only when DISCLAIMER.md changes in substance: everyone is asked once more
DISCLAIMER_URL = "https://github.com/joshualytle/Gnomonkit/blob/main/DISCLAIMER.md"
MAX_OFFERS = 2             # first-run setup is offered at this many session starts, then one line
MACHINE_KINDS = ("personal", "work", "unknown")   # unknown: the user would rather not say; treated as work
MEMORY_MODES = ("auto", "on", "off")   # memory.history, memory.lessons; auto: off when MemPalace is set up here


def gnomonkit_home() -> str:
    raw = (os.environ.get("GNOMONKIT_HOME") or "").strip()
    return os.path.abspath(os.path.expanduser(raw)) if raw else os.path.join(os.path.expanduser("~"), ".gnomonkit")


def core_dir() -> str:
    """The core plugin's root (the folder holding gnomonkit.py, hooks/ and skills/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def gnomonkit_script() -> str:
    return os.path.join(core_dir(), "gnomonkit.py")


def python_name() -> str:
    """The interpreter running now, as a command name: a bare `python` is missing on python3-only systems."""
    return os.path.splitext(os.path.basename(sys.executable or ""))[0] or "python"


def command_line(*args: str) -> str:
    """The full command to run gnomonkit with these arguments, for messages an agent or a person acts on."""
    return " ".join(['%s "%s"' % (python_name(), gnomonkit_script())] + list(args))


def core_version() -> str:
    try:
        with open(os.path.join(core_dir(), ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "?")
    except (OSError, ValueError):
        return "?"


class CommandError(Exception):
    """A refusal or a bad argument the person should read. gnomonkit.py prints it and exits with `code`."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


# ---- files and folders -------------------------------------------------------------------------
def ensure_dir(folder: str, mode: int = 0o700) -> str:
    """Create `folder` and any missing parents. On POSIX each folder made here is 0700."""
    folder = os.path.abspath(folder)
    missing, p = [], folder
    while not os.path.isdir(p):
        missing.append(p)
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    for d in reversed(missing):
        try:
            os.mkdir(d, mode)
        except FileExistsError:
            pass
        if os.name != "nt":
            try:
                os.chmod(d, mode)
            except OSError:
                pass
    return folder


def atomic_write(path: str, data, mode: int = 0o600) -> None:
    """Write `data` (str as UTF-8, or bytes) to `path` so a reader sees the old file or the new one, never
    half of one: a temp file in the same folder, flushed, then os.replace. On POSIX the file is `mode`
    (0600 by default; mkstemp creates it that way, so it is never readable by others, even briefly)."""
    import tempfile
    path = os.path.abspath(path)
    folder = ensure_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name != "nt":
            os.chmod(tmp, mode)
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                # Windows refuses to replace a file another process has open for a moment
                # (an editor, a virus scanner). Retry briefly, then give up loudly.
                if os.name != "nt" or attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---- paths -------------------------------------------------------------------------------------
def resolve_path(p: str) -> str:
    """For storing: absolute, ~ expanded, `..` and symlinks resolved, one separator throughout."""
    p = os.path.realpath(os.path.abspath(os.path.expanduser(str(p).strip())))
    if os.sep == "\\":
        p = p.replace("/", "\\")
    return p


def normalise_path(p: str) -> str:
    """For comparing: resolve_path, then case-folded on Windows (its file system ignores case)."""
    p = resolve_path(p)
    return p.casefold() if os.name == "nt" else p


def path_within(path: str, root: str) -> bool:
    """True when `path` is `root` or anything under it, after normalising both."""
    a, b = normalise_path(path), normalise_path(root)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


# ---- TOML in -----------------------------------------------------------------------------------
def parse_toml(text: str) -> dict:
    """tomllib on Python 3.11+. Older Pythons get a small parser for what these files use: tables,
    arrays of tables, dotted and quoted keys, strings (basic and literal), integers, floats, booleans,
    and arrays (also over several lines). Raises ValueError on what it can't read."""
    try:
        import tomllib  # Python 3.11+
        return tomllib.loads(text)
    except ModuleNotFoundError:
        pass
    return _fallback_loads(text)


def load_toml(path: str) -> dict:
    """The file's tables. OSError when it can't be read, ValueError when it isn't TOML."""
    with open(path, encoding="utf-8") as fh:
        return parse_toml(fh.read())


def read_toml(path: str) -> dict | None:
    """Like load_toml, but None instead of an error: for hooks, which must never fail."""
    try:
        return load_toml(path)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


_ESC = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
_NUM = re.compile(r"[+-]?(\d[\d_]*)(\.\d[\d_]*)?([eE][+-]?\d+)?$")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}([Tt ][\d:.]+([Zz]|[+-]\d{2}:\d{2})?)?$|\d{2}:\d{2}(:\d{2}(\.\d+)?)?$")


class _More(ValueError):
    """An array is still open at the end of the text: the caller appends the next line."""


def _skip(s: str, i: int, newlines: bool) -> int:
    while i < len(s):
        if s[i] in " \t" or (newlines and s[i] in "\r\n"):
            i += 1
        elif newlines and s[i] == "#":
            while i < len(s) and s[i] != "\n":
                i += 1
        else:
            break
    return i


def _string(s: str, i: int):
    q = s[i]
    if s.startswith(q * 3, i):
        raise ValueError("multi-line strings are not supported without Python 3.11")
    i += 1
    out = []
    while i < len(s):
        ch = s[i]
        if ch == q:
            return "".join(out), i + 1
        if ch == "\n":
            break
        if q == '"' and ch == "\\":
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
        out.append(ch)
        i += 1
    raise ValueError("unterminated string")


def _value(s: str, i: int):
    i = _skip(s, i, False)
    if i >= len(s):
        raise ValueError("missing value")
    ch = s[i]
    if ch in "\"'":
        return _string(s, i)
    if ch == "[":
        items, i = [], i + 1
        while True:
            i = _skip(s, i, True)
            if i >= len(s):
                raise _More("open array")
            if s[i] == "]":
                return items, i + 1
            v, i = _value(s, i)
            items.append(v)
            i = _skip(s, i, True)
            if i >= len(s):
                raise _More("open array")
            if s[i] == ",":
                i += 1
            elif s[i] != "]":
                raise ValueError("bad array")
    m = re.match(r"[^\s,\]#]+", s[i:])
    if not m:
        raise ValueError("bad value")
    tok, end = m.group(0), i + m.end()
    if tok in ("true", "false"):
        return tok == "true", end
    if tok in ("inf", "+inf", "-inf", "nan", "+nan", "-nan"):
        return float(tok), end
    if _NUM.match(tok):
        t = tok.replace("_", "")
        return (float(t) if any(c in t for c in ".eE") else int(t)), end
    if _DATE.match(tok):
        return tok, end   # dates stay text here; nothing in these files compares them
    raise ValueError("unsupported value %r" % tok[:20])


def _split_key(text: str) -> list:
    parts, i, s = [], 0, text.strip()
    while i < len(s):
        i = _skip(s, i, False)
        if s[i] in "\"'":
            k, i = _string(s, i)
        else:
            m = re.match(r"[A-Za-z0-9_-]+", s[i:])
            if not m:
                raise ValueError("bad key")
            k, i = m.group(0), i + m.end()
        parts.append(k)
        i = _skip(s, i, False)
        if i < len(s):
            if s[i] != ".":
                raise ValueError("bad key")
            i += 1
    if not parts:
        raise ValueError("empty key")
    return parts


def _descend(root: dict, parts: list) -> dict:
    cur = root
    for p in parts:
        nxt = cur.setdefault(p, {})
        if isinstance(nxt, list) and nxt and isinstance(nxt[-1], dict):
            nxt = nxt[-1]
        if not isinstance(nxt, dict):
            raise ValueError("key %r is not a table" % p)
        cur = nxt
    return cur


def _key_end(line: str) -> int:
    """Index of the `=` that ends the key, outside any quotes; -1 when there is none."""
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "\"'":
            q = ch
        elif ch == "=":
            return i
    return -1


def _fallback_loads(text: str) -> dict:
    data: dict = {}
    cur = data
    lines = text.splitlines()
    n = 0
    while n < len(lines):
        line = lines[n].strip()
        n += 1
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            arr = line.startswith("[[")
            close = line.find("]]" if arr else "]")
            rest = line[close + (2 if arr else 1):].strip() if close > 0 else "x"
            if close < 0 or (rest and not rest.startswith("#")):
                raise ValueError("bad table header on line %d" % n)
            parts = _split_key(line[2 if arr else 1:close])
            if arr:
                parent = _descend(data, parts[:-1])
                lst = parent.setdefault(parts[-1], [])
                if not isinstance(lst, list):
                    raise ValueError("line %d: %s is not an array of tables" % (n, parts[-1]))
                cur = {}
                lst.append(cur)
            else:
                cur = _descend(data, parts)
            continue
        eq = _key_end(line)
        if eq < 0:
            raise ValueError("line %d is not key = value" % n)
        parts = _split_key(line[:eq])
        chunk = line[eq + 1:]
        while True:
            try:
                value, end = _value(chunk, 0)
                break
            except _More:
                if n >= len(lines):
                    raise ValueError("unterminated array") from None
                chunk += "\n" + lines[n]
                n += 1
        tail = chunk[end:].strip()
        if tail and not tail.startswith("#"):
            raise ValueError("line %d: unexpected text after the value" % n)
        _descend(cur, parts[:-1])[parts[-1]] = value
    return data


# ---- TOML out ----------------------------------------------------------------------------------
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+$")


def _toml_str(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch in "\b\t\n\f\r":
            out.append({"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return "".join(out) + '"'


def _toml_key(k) -> str:
    k = str(k)
    return k if _BARE_KEY.match(k) else _toml_str(k)


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:
            return "nan"
        if v in (float("inf"), float("-inf")):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return _toml_str(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join("%s = %s" % (_toml_key(k), _toml_value(x)) for k, x in v.items()) + " }"
    if hasattr(v, "isoformat"):   # date, time, datetime (from tomllib, kept as they were)
        return v.isoformat()
    raise TypeError("can't write %s to TOML" % type(v).__name__)


def _is_table_array(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def _ordered(table: dict, known) -> list:
    keys = [k for k in known if k in table] + [k for k in table if k not in known]
    return [(k, table[k]) for k in keys]


def _blank(out: list) -> None:
    if out and out[-1] != "":
        out.append("")


def _dotted(path: list) -> str:
    return ".".join(_toml_key(p) for p in path)


def _is_leaf(v) -> bool:
    return not isinstance(v, dict) and not _is_table_array(v)


def _emit_body(out: list, path: list, table: dict, known=(), child_known=None) -> None:
    """The key = value lines of one table, then its sub-tables and arrays of tables."""
    items = _ordered(table, known)
    for k, v in items:
        if _is_leaf(v):
            out.append("%s = %s" % (_toml_key(k), _toml_value(v)))
    for k, v in items:
        if isinstance(v, dict):
            _emit_table(out, path + [k], v, (child_known or {}).get(k, ()))
        elif _is_table_array(v):
            for item in v:
                _blank(out)
                out.append("[[%s]]" % _dotted(path + [k]))
                _emit_body(out, path + [k], item)


def _emit_table(out: list, path: list, table: dict, known=()) -> None:
    # A table holding only sub-tables needs no header of its own; an empty one keeps its header.
    if any(_is_leaf(v) for v in table.values()) or not table:
        _blank(out)
        out.append("[%s]" % _dotted(path))
    _emit_body(out, path, table, known)


def dumps(data: dict, layout=(), header: str = "") -> str:
    """TOML text in a fixed layout. `layout` is [(table, [keys...]), ...]: those tables in that order,
    their known keys first in that order, then whatever else each holds; tables the layout doesn't
    name follow, in the order they appear. Table "" is the top level (its keys come before any
    table, as TOML requires). Writes strings, integers, floats, booleans, lists, sub-tables, arrays
    of tables and (unchanged) dates. Comments are not kept: the caller says so and keeps a backup."""
    order = {t: list(keys) for t, keys in layout}
    out: list = [("# " + line).rstrip() for line in header.splitlines()]
    if out:
        out.append("")
    known_top = order.get("", []) + [t for t, _ in layout if t]
    _emit_body(out, [], data, known_top, child_known=order)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


# ---- the personal profile schema ---------------------------------------------------------------
class Setting:
    """One key of the profile. kind: choice | int | number | bool | list | text | path | repo.
    store: where `gnomonkit profile set` writes it (personal | machine | subscription).
    option: the plugin option that, when set, wins over the profile (CLAUDE_PLUGIN_OPTION_<option>,
    then GNOMONKIT_<option>)."""

    __slots__ = ("key", "kind", "label", "default", "choices", "group", "option", "store", "max_len")

    def __init__(self, key, kind, label, default=None, choices=(), group="default", option=None,
                 store="personal", max_len=120):
        self.key, self.kind, self.label, self.default = key, kind, label, default
        self.choices, self.group, self.option, self.store, self.max_len = tuple(choices), group, option, store, max_len

    @property
    def table(self) -> str:
        return self.key.split(".", 1)[0]

    @property
    def name(self) -> str:
        return self.key.split(".", 1)[1]


DEFAULT_ALWAYS_CONFIRM = ("deleting data", "force-pushes and history rewrites", "publishing anything")

SCHEMA = (
    # The default set: asked in one message at first-run setup, each with its default shown.
    Setting("communication.decisions", "choice", "How decisions are presented", "numbered", ("numbered", "prose")),
    Setting("communication.reply_length", "choice", "Reply length", "normal", ("short", "normal", "detailed")),
    Setting("working.ask_before_acting", "choice", "Ask before acting", "risky", ("always", "risky", "rarely")),
    Setting("working.remote", "choice", "Work remotely or from the phone a lot (yes: sessions start with Remote "
            "Control; saved by `setup remote on`)", "no", ("yes", "no")),
    Setting("context.context_budget", "int", "Context budget in tokens (0 = off)", 250000, option="CONTEXT_BUDGET"),
    Setting("context.max_compactions", "int", "Automatic compactions before the handoff is written (0 = off)", 2,
            option="MAX_COMPACTIONS"),
    Setting("context.manual_compactions", "int",
            "Manual /compact runs allowed after that before a fresh session is advised", 1,
            option="MANUAL_COMPACTIONS"),
    Setting("machine.shell", "choice", "Shell that commands are written for (asked only when several exist)",
            None, store="machine"),
    Setting("machine.kind", "choice", "Personal or work machine (unknown, for rather not say, counts as work)",
            None, MACHINE_KINDS, store="machine"),
    Setting("delivery.commit_email", "choice", "Commit e-mail", "noreply", ("noreply", "any")),
    Setting("delivery.prs", "choice", "Pull requests", "always", ("always", "direct-ok")),
    Setting("delivery.test_style", "choice", "Tests", "match-repo", ("test-first", "tests-after", "match-repo")),
    Setting("delivery.merge", "choice", "Merging green pull requests", "agent", ("agent", "ask", "ask-for")),
    Setting("delivery.merge_ask_for", "list",
            "With merge ask-for: what still needs you (path globs, or label:<name>)", [], group="advanced"),
    Setting("safety.always_confirm", "list", "Always confirm before", list(DEFAULT_ALWAYS_CONFIRM)),
    Setting("memory.lessons", "choice", "Lessons from past sessions (auto: on unless MemPalace is set up here)",
            "auto", MEMORY_MODES),
    Setting("orchestrator.mode", "choice",
            "Skill orchestrator: a model reads the last few messages and suggests skills (needs the interjector "
            "plugin; claude uses your login, api sends the messages to Anthropic, ollama stays on this machine)",
            "off", ("off", "claude", "api", "ollama")),
    # The advanced set: shown when the user asks for it.
    Setting("communication.explanation_level", "choice", "Explanations", "standard", ("brief", "standard", "teach"),
            group="advanced"),
    Setting("working.subagents", "choice", "Subagents", "normal", ("sparing", "normal", "liberal"), group="advanced"),
    Setting("dispatch.primary_client", "choice", "How a primary session dispatch starts opens (any: background, "
            "kept running with Remote Control; terminal: background, always running; vscode: a VS Code tab)",
            "any", ("any", "terminal", "vscode"), group="advanced"),
    Setting("orchestrator.model", "text", "Orchestrator model (empty: haiku for claude and api; required for ollama)",
            None, group="advanced", max_len=80),
    Setting("plan.label", "text", "Plan name (written to the subscription file)", None, group="advanced",
            store="subscription", max_len=60),
    Setting("plan.monthly_usd", "number", "Plan price per month in USD (written to the subscription file)", None,
            group="advanced", store="subscription"),
    Setting("delivery.ci", "choice", "CI", None, ("self-hosted", "hosted", "none"), group="advanced"),
    Setting("delivery.ci_runner_label", "text", "Self-hosted runner label", None, group="advanced", max_len=60),
    Setting("delivery.hosted_minutes_budget", "int", "Hosted CI minutes per month", None, group="advanced"),
    Setting("code.style", "text", "Code style, in one short line", None, group="advanced", max_len=200),
    Setting("paths.scratch_root", "path", "Scratch folder", "~/.gnomonkit/scratch", group="advanced",
            option="SCRATCH_ROOT"),
    Setting("alerts.destination", "choice", "Where alerts go", None, ("slack", "email", "none"), group="advanced"),
    Setting("alerts.issues_only", "bool", "Alerts only for issues", False, group="advanced"),
    Setting("corrections.repo", "repo", "Private repo (owner/name) for learned-trigger issues", None,
            group="advanced", option="CORRECTIONS_REPO"),
    Setting("memory.history", "choice", "Gnomonkit's own history index (auto: on unless MemPalace is set up here)",
            "auto", MEMORY_MODES, group="advanced"),
    Setting("memory.exclude_projects", "list", "Projects the history index never reads (names or folders)", [],
            group="advanced"),
    Setting("memory.history_keep_days", "int", "Days of past sessions the history index keeps (0 = all)", 365,
            group="advanced"),
    Setting("memory.history_max_mb", "int", "Largest the history index grows, in MB (0 = no cap)", 1024,
            group="advanced"),
    Setting("work.repo", "repo", "Private repo (owner/name) for work items that cross projects", None,
            group="advanced", option="WORK_REPO"),
    Setting("work.allow_public", "list", "Public repos (owner/name) where work items may still be kept", [],
            group="advanced"),
    Setting("work.stale_hours", "int", "Hours with no comment before a claimed work item shows as stale", 24,
            group="advanced"),
    # Recorded by setup, not asked as a question.
    Setting("acknowledged.disclaimer", "choice", "Disclaimer acknowledged (version)", None, (DISCLAIMER_VERSION,),
            group="record"),
)
SCHEMA_BY_KEY = {s.key: s for s in SCHEMA}


def personal_layout() -> list:
    """[(table, [keys])] for the personal profile file, in schema order."""
    out: dict = {}
    for s in SCHEMA:
        if s.store == "personal":
            out.setdefault(s.table, []).append(s.name)
    return list(out.items())


PERSONAL_HEADER = ("Gnomonkit personal profile. See docs/PROFILES.md.\n"
                   "Written by `gnomonkit profile set`, which checks every value. Comments are not kept;\n"
                   "the previous version is saved beside this file as personal.toml.bak.")

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TRUE, _FALSE = ("true", "yes", "on", "1"), ("false", "no", "off", "0")


def _choices_text(s: Setting) -> str:
    return " | ".join(s.choices)


def coerce(s: Setting, raw: str, choices=None):
    """A command-line value for `s`, as the type the file stores. ValueError says what is valid."""
    raw = str(raw).strip()
    kind = s.kind
    if kind == "choice":
        allowed = tuple(choices) if choices is not None else s.choices
        v = raw.lower()
        if v not in allowed:
            raise ValueError("%s must be one of: %s" % (s.key, " | ".join(allowed) or "(none found)"))
        return v
    if kind == "int":
        t = raw.lower().replace("_", "").replace(",", "")
        mult = 1000 if t.endswith("k") else 1
        t = t[:-1] if mult > 1 else t
        if not t.isdigit():
            raise ValueError("%s must be a whole number, 0 or more (for example 250000 or 250k)" % s.key)
        return int(t) * mult
    if kind == "number":
        t = raw.lstrip("$").replace(",", "")
        try:
            v = float(t)
        except ValueError:
            v = -1.0
        if not (v >= 0) or v == float("inf"):
            raise ValueError("%s must be a number, 0 or more (for example 20 or 19.99)" % s.key)
        return int(v) if v == int(v) else v
    if kind == "bool":
        if raw.lower() in _TRUE:
            return True
        if raw.lower() in _FALSE:
            return False
        raise ValueError("%s must be true or false" % s.key)
    if kind == "list":
        if raw.startswith("["):
            try:
                items = json.loads(raw)
            except ValueError:
                raise ValueError('%s: a list is items separated by commas, or JSON such as ["a", "b"]' % s.key) from None
            if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
                raise ValueError("%s must be a list of text items" % s.key)
        else:
            items = raw.split(",")
        items = [x.strip() for x in items if x.strip()]
        v = items
    elif kind == "path":
        if not raw:
            raise ValueError("%s must be a folder path" % s.key)
        v = resolve_path(raw)
    else:  # text, repo
        v = raw
    problem = check_value(s, v)
    if problem:
        raise ValueError(problem)
    return v


def check_value(s: Setting, v) -> str | None:
    """None when `v` is a valid stored value for `s`, else what is wrong with it."""
    kind = s.kind
    if kind == "choice":
        if s.store == "machine" and not s.choices:   # machine.shell: the choices are what is installed
            return None if isinstance(v, str) and v else "%s must be a shell name" % s.key
        return None if v in s.choices else "%s must be one of: %s" % (s.key, _choices_text(s))
    if kind == "int":
        ok = isinstance(v, int) and not isinstance(v, bool) and v >= 0
        return None if ok else "%s must be a whole number, 0 or more" % s.key
    if kind == "number":
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
        return None if ok else "%s must be a number, 0 or more" % s.key
    if kind == "bool":
        return None if isinstance(v, bool) else "%s must be true or false" % s.key
    if kind == "list":
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            return "%s must be a list of text items" % s.key
        if len(v) > 20 or any(len(x) > s.max_len or _CONTROL.search(x) for x in v):
            return "%s: at most 20 items, each one line of up to %d characters" % (s.key, s.max_len)
        return None
    if not isinstance(v, str):
        return "%s must be text" % s.key
    if _CONTROL.search(v) or len(v) > (400 if kind == "path" else s.max_len):
        return "%s must be one line of up to %d characters" % (s.key, 400 if kind == "path" else s.max_len)
    if kind == "repo" and not _REPO.match(v):
        return "%s must look like owner/name" % s.key
    if kind == "path":
        if not os.path.isabs(os.path.expanduser(v)):
            return "%s must be an absolute folder path" % s.key
        r = resolve_path(v)
        if os.path.dirname(r) == r:
            return "%s can't be a drive or file-system root" % s.key
        if normalise_path(r) == normalise_path(os.path.expanduser("~")):
            return "%s can't be the home folder itself" % s.key
    if kind == "text" and not v.strip():
        return "%s can't be empty" % s.key
    return None


def validate_profile(data) -> list:
    """Problems with a loaded personal profile, one line each; [] when it passes the schema. Keys and
    tables the schema doesn't know are allowed (they are kept when the file is rewritten)."""
    if not isinstance(data, dict):
        return ["the profile is not a TOML document"]
    problems = []
    for table in {s.table for s in SCHEMA if s.store == "personal"}:
        if table in data and not isinstance(data[table], dict):
            problems.append("[%s] must be a table" % table)
    for s in SCHEMA:
        if s.store != "personal":
            continue
        t = data.get(s.table)
        if isinstance(t, dict) and s.name in t:
            problem = check_value(s, t[s.name])
            if problem:
                problems.append(problem)
    return problems


# ---- where the profile is, and what it says ----------------------------------------------------
def onboarding_path() -> str:
    return os.path.join(gnomonkit_home(), "onboarding.json")


def read_onboarding() -> dict:
    """First-run state: {"state": "done"|"skipped", "offers": n, "profile_option": path}. {} if none."""
    try:
        with open(onboarding_path(), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_onboarding(state: dict) -> None:
    state = dict(state, updated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    atomic_write(onboarding_path(), json.dumps(state, indent=1, sort_keys=True) + "\n")


def default_personal_path() -> str:
    return os.path.join(gnomonkit_home(), "personal.toml")


def personal_profile_path(use_record: bool = True) -> tuple:
    """(path, source). In order: the core plugin's personal_profile option; the option as the profile
    hook last saw it (plugin options reach hooks but not the agent's shell, so the hook records it in
    onboarding.json); GNOMONKIT_PERSONAL_PROFILE; then ~/.gnomonkit/personal.toml."""
    p = (os.environ.get("CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE") or "").strip()
    if p:
        return os.path.expanduser(p), "plugin option personal_profile"
    if use_record:
        rec = read_onboarding().get("profile_option")
        if isinstance(rec, str) and rec.strip():
            return os.path.expanduser(rec.strip()), "plugin option personal_profile (recorded at session start)"
    p = (os.environ.get("GNOMONKIT_PERSONAL_PROFILE") or "").strip()
    if p:
        return os.path.expanduser(p), "GNOMONKIT_PERSONAL_PROFILE"
    return default_personal_path(), "default location"


_CACHE: dict = {}


def load_personal() -> dict | None:
    """The personal profile's tables, or None when there is none or it can't be read. Never raises."""
    try:
        path, _ = personal_profile_path()
    except Exception:
        return None
    if path not in _CACHE:
        _CACHE[path] = read_toml(path)
    return _CACHE[path]


def lookup(data, key: str):
    cur = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def profile_value(key: str):
    """What the personal profile says for a dotted key, or None. Never raises: hooks call this."""
    try:
        return lookup(load_personal(), key)
    except Exception:
        return None


def checked_profile_value(key: str):
    """profile_value(key) when the value passes the schema, else None. Never raises: for hooks and
    scripts that fall back to the profile when their plugin option is unset."""
    try:
        v = profile_value(key)
        return v if v is not None and check_value(SCHEMA_BY_KEY[key], v) is None else None
    except Exception:
        return None


def setting(key: str) -> tuple:
    """(value, source) with the precedence every reader uses: plugin option, profile, default."""
    s = SCHEMA_BY_KEY[key]
    if s.option:
        for name in ("CLAUDE_PLUGIN_OPTION_" + s.option, "GNOMONKIT_" + s.option):
            raw = (os.environ.get(name) or "").strip()
            if raw:
                return raw, name
    v = profile_value(key)
    if v is not None:
        return v, "personal profile"
    return s.default, "default"


# ---- this machine: its kind, and the memory backend set up here ------------------------------
MEMPALACE_URL = "https://github.com/MemPalace/mempalace"
MEMPALACE_CACHE_SECONDS = 3600


def machine_path() -> str:
    return os.path.join(gnomonkit_home(), "machine.toml")


def machine_kind() -> str | None:
    """This machine's kind from the machine profile (never the personal one: one person can have both
    kinds): "personal", "work", "unknown", or None when it hasn't been asked yet. Anything but
    "personal" is treated as a work machine. Never raises."""
    try:
        v = (read_toml(machine_path()) or {}).get("kind")
        return v if v in MACHINE_KINDS else None
    except Exception:
        return None


def is_personal_machine() -> bool:
    return machine_kind() == "personal"


def claude_home() -> str:
    """Claude Code's config folder: CLAUDE_CONFIG_DIR, else ~/.claude."""
    return (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip() or os.path.join(os.path.expanduser("~"), ".claude")


def claude_json_path() -> str:
    """Claude Code's user config with its MCP servers: .claude.json in CLAUDE_CONFIG_DIR, else ~/.claude.json."""
    d = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return os.path.join(d, ".claude.json") if d else os.path.join(os.path.expanduser("~"), ".claude.json")


def memory_backend_path() -> str:
    return os.path.join(gnomonkit_home(), "memory-backend.json")


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _mempalace_servers(servers) -> list:
    """The names of the MCP servers in `servers` ({name: config}) whose name or command line (command,
    args or url) contains "mempalace", in any case."""
    found = []
    if not isinstance(servers, dict):
        return found
    for name, cfg in servers.items():
        parts = [str(name)]
        if isinstance(cfg, dict):
            parts += [str(cfg.get(k) or "") for k in ("command", "url")]
            if isinstance(cfg.get("args"), list):
                parts += [str(a) for a in cfg["args"]]
        if "mempalace" in " ".join(parts).lower():
            found.append(str(name))
    return found


def _repo_root(folder: str) -> str | None:
    d = folder
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _detect_mempalace(cwd: str) -> dict:
    import shutil
    where = []
    exe = shutil.which("mempalace")
    if exe:
        where.append("on PATH: %s" % exe)
    mcp = []
    cfg_path = claude_json_path()
    cfg = _read_json(cfg_path)
    if isinstance(cfg, dict):
        mcp += ["%s: mcpServers.%s" % (cfg_path, n) for n in _mempalace_servers(cfg.get("mcpServers"))]
        projects = cfg.get("projects")
        for proj, pcfg in (projects.items() if isinstance(projects, dict) else ()):
            if isinstance(pcfg, dict):
                mcp += ["%s: projects[%s].mcpServers.%s" % (cfg_path, proj, n)
                        for n in _mempalace_servers(pcfg.get("mcpServers"))]
    folders, root = [cwd], _repo_root(cwd)
    if root and normalise_path(root) != normalise_path(cwd):
        folders.append(root)
    for folder in folders:
        p = os.path.join(folder, ".mcp.json")
        d = _read_json(p)
        if isinstance(d, dict):
            mcp += ["%s: %s" % (p, n) for n in _mempalace_servers(d.get("mcpServers"))]
    record = _read_json(os.path.join(claude_home(), "plugins", "installed_plugins.json"))
    plugins = record.get("plugins") if isinstance(record, dict) else None
    seen = set()
    for key, entries in (plugins.items() if isinstance(plugins, dict) else ()):
        for e in entries if isinstance(entries, list) else [entries]:
            path = e.get("installPath") if isinstance(e, dict) else None
            if not isinstance(path, str) or path in seen:
                continue
            seen.add(path)
            # A plugin declares servers in .mcp.json (wrapped in mcpServers or not) or inline in plugin.json.
            d = _read_json(os.path.join(path, ".mcp.json"))
            if isinstance(d, dict):
                servers = d["mcpServers"] if isinstance(d.get("mcpServers"), dict) else d
                mcp += ["plugin %s (.mcp.json): %s" % (key, n) for n in _mempalace_servers(servers)]
            m = _read_json(os.path.join(path, ".claude-plugin", "plugin.json"))
            if isinstance(m, dict):
                mcp += ["plugin %s (plugin.json): %s" % (key, n) for n in _mempalace_servers(m.get("mcpServers"))]
    return {"installed": bool(exe), "mcp": bool(mcp), "where": where + mcp}


def mempalace_status(cwd: str | None = None, refresh: bool = False) -> dict:
    """Whether MemPalace is set up here: {"installed": bool, "mcp": bool, "where": [str, ...]}.

    installed: a `mempalace` executable is on PATH. mcp: an MCP server whose name or command line
    contains "mempalace" is registered with Claude Code, in .claude.json (CLAUDE_CONFIG_DIR, else
    ~/.claude.json: the top-level mcpServers and every projects[*].mcpServers), in a .mcp.json at `cwd`
    or its repository root, or by an installed plugin (installed_plugins.json installPaths). where:
    one line per thing found. Hooks are not looked at: they may be off on purpose on a machine that
    still uses MemPalace for recall.

    Cached per folder for an hour in ~/.gnomonkit/memory-backend.json so per-prompt hooks stay cheap;
    refresh=True looks again. Never raises: on any failure it says nothing was found."""
    try:
        cwd = os.path.abspath(cwd or os.getcwd())
        key, now = normalise_path(cwd), time.time()
        cache = _read_json(memory_backend_path())
        entries = cache.get("entries") if isinstance(cache, dict) and cache.get("version") == 1 else None
        entries = entries if isinstance(entries, dict) else {}
        hit = entries.get(key)
        if not refresh and isinstance(hit, dict) and isinstance(hit.get("at"), (int, float)) \
                and 0 <= now - hit["at"] < MEMPALACE_CACHE_SECONDS and isinstance(hit.get("status"), dict):
            st = hit["status"]
            where = st.get("where")
            return {"installed": st.get("installed") is True, "mcp": st.get("mcp") is True,
                    "where": [str(w) for w in where] if isinstance(where, list) else []}
        status = _detect_mempalace(cwd)
        entries[key] = {"at": now, "status": status}
        if len(entries) > 32:   # keep the file small: the folders checked most recently
            keep = sorted(entries, key=lambda k: entries[k].get("at", 0) if isinstance(entries[k], dict) else 0)[-32:]
            entries = {k: entries[k] for k in keep}
        try:
            atomic_write(memory_backend_path(), json.dumps({"version": 1, "entries": entries}, indent=1) + "\n")
        except Exception:
            pass
        return status
    except Exception:
        return {"installed": False, "mcp": False, "where": []}


# ---- Gnomonkit's own memory: on, off, or auto ----------------------------------------------------
_ON_WORDS = ("on", "1", "true", "yes")
_OFF_WORDS = ("off", "0", "false", "no")


def memory_feature(key: str, env: str, cwd: str | None = None) -> tuple:
    """(on, why) for one of Gnomonkit's own memory features: "memory.history" (the history index,
    env GNOMONKIT_HISTORY) or "memory.lessons" (the lessons hook, env GNOMONKIT_LESSONS).

    The first of these that says on or off decides: the environment variable (also 1/0, true/false,
    yes/no), then `key` in the personal profile. Otherwise (unset, or auto, the default) it is auto:
    off when MemPalace is set up here (mempalace_status(cwd): a `mempalace` on PATH, or an MCP server
    registered for it), so two memory tools don't both record and inject, and on anywhere else. why
    is a few words for status lines. Never raises: a detection that fails counts as MemPalace not set
    up, so the feature stays on."""
    try:
        raw = (os.environ.get(env) or "").strip().lower()
        if raw in _ON_WORDS or raw in _OFF_WORDS:
            return raw in _ON_WORDS, "%s=%s" % (env, raw)
        v = checked_profile_value(key)
        if v in ("on", "off"):
            return v == "on", "%s = %s in the personal profile" % (key, v)
    except Exception:  # noqa: BLE001 - a hook calls this: never raise
        return True, "auto"
    try:
        st = mempalace_status(cwd)
        found = bool(st.get("installed") or st.get("mcp"))
    except Exception:  # noqa: BLE001
        found = False
    return (False, "auto, and MemPalace is set up here") if found else (True, "auto, and MemPalace isn't set up here")


# ---- a person at a terminal --------------------------------------------------------------------
# On Windows isatty() is True for the NUL device too (`< NUL`, `> NUL`), so a redirected agent shell would
# pass it: there a terminal must also be a console, which GetConsoleMode tells.
_CHECK_CONSOLE = os.name == "nt"


def _is_console(stream) -> bool:
    """Windows: whether the stream's handle is a console. Any failure means it is not."""
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = wintypes.DWORD()
        return bool(ctypes.windll.kernel32.GetConsoleMode(wintypes.HANDLE(handle), ctypes.byref(mode)))
    except Exception:
        return False


def is_terminal(stdin=None, stdout=None) -> bool:
    """Whether stdin and stdout are both a terminal: isatty() on each, and on Windows a console as well."""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    try:
        if not (stdin and stdout and stdin.isatty() and stdout.isatty()):
            return False
    except (AttributeError, ValueError, OSError):
        return False
    return not _CHECK_CONSOLE or (_is_console(stdin) and _is_console(stdout))


def require_tty(confirm_word: str, prompt: str | None = None, stdin=None, stdout=None) -> None:
    """Go on only when stdin and stdout are both a terminal and the person types `confirm_word`.
    Otherwise raise CommandError (exit 2) and change nothing. For permission changes (orchestration
    grants, folder trust): an agent's shell has no terminal, so it can't answer this."""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout
    if not is_terminal(stdin, stdout):
        raise CommandError("refused: this changes a permission, so it runs only in a terminal where you type the "
                           "answer yourself (stdin and stdout must both be a terminal; an agent's shell is not). "
                           "Nothing was changed.")
    stdout.write(prompt if prompt is not None else "Type %s to confirm: " % confirm_word)
    stdout.flush()
    answer = stdin.readline()
    if answer.strip() != confirm_word:
        raise CommandError("not confirmed (the word was %s); nothing was changed." % confirm_word)


# ---- subcommand discovery ----------------------------------------------------------------------
def command_modules() -> list:
    """[(name, module or None, error or None)] for every module in this package not starting with "_",
    sorted by name. A module that fails to import is reported, not raised, so one broken area never
    takes the others down."""
    import importlib
    import pkgutil
    pkg_name = __package__ or "gnomonkit_cmds"
    pkg = importlib.import_module(pkg_name)
    out = []
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        try:
            out.append((info.name, importlib.import_module(pkg_name + "." + info.name), None))
        except Exception as e:  # noqa: BLE001 - reported to the person, never fatal
            out.append((info.name, None, "%s: %s" % (type(e).__name__, e)))
    return out
