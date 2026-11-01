"""Session roles for session-dispatch: which session is a project's primary, which are subs, and when a sub
is finished.

- **primary**: the session the user talks to; at most one open (running) per project.
- **sub**: started by another session for one task, named by the naming scheme's sub template
  (`sub:<parent>/<task>` by default; ij_naming.scheme()); it reports back and is done.

Dispatch records every session it starts in $GNOMONKIT_HOME/dispatch/roles.json, keyed by the id
`claude --bg` printed (or `name:<title>` until that id is known): role, project, task, name, parent (the
requesting session's title, when known), parent_id (the requesting session's id, which lets the spawn
grant cover messaging and stopping it), started, pr (its URL when gh could give one), done, merged,
stopped, bg (started with --bg) and client. A background primary the user has opened is re-recorded by
sync_opened as client "tab", bg false, with opened (when) and opened_how. A session dispatch didn't start has no record, has no role, and is
never stopped automatically. A file that can't be parsed is kept aside as roles.json.bad-<time>, never
written over.

A sub is finished when it ran `dispatch.py done` (or its recorded pull request merged, read with
`gh pr view <pr> --json state`) and it is idle, not busy, not waiting on the user, nothing new has reached
it since its last reply, and that reply asks the user nothing (ij_history.asks_user). Work that reaches a
sub after it reported done (a prompt, or a message from another session) takes `done` back. Only a sub
dispatch started with --bg, still listed as a background session, ever qualifies: never an interactive
session, a primary, or an unknown one. The stop itself is dispatch's own `stop` path (`claude stop <id>`,
after the grants check); this module decides, it never stops or deletes anything.

An empty session is an interactive one in the list, idle, whose transcript (<Claude config folder>/projects/<slug>/
<session id>.jsonl) is missing or holds neither a user prompt nor a reply: usually an IDE tab opened and
left. `list` and the board hide them behind a one-line count.

Settings, read from the environment, else the `[dispatch]` table of the personal profile, else the
default: auto_stop (GNOMONKIT_DISPATCH_AUTO_STOP, default true), stop_timeout_minutes
(GNOMONKIT_DISPATCH_STOP_TIMEOUT_MINUTES, default 15, at most 1440), primary_client
(GNOMONKIT_DISPATCH_PRIMARY_CLIENT, "any", "terminal" or "vscode", default "any"), and the model and
effort a session of each role starts with: primary_model (default "", the user's own default model),
primary_effort (default "medium"), sub_model (default "sonnet"), sub_effort (default "medium"). A model
is a Claude Code alias or model id; "" leaves the choice to the user's settings, and so does an effort of
"". Without them every dispatched session, however small its task, inherits the user's default model
at the user's default effort.

A primary opened as a VS Code tab has no id until the user sends its first prompt, so its record stays
under `name:<title>` (client "vscode", bg false) and is matched to the tab by that title, which the
naming hook gives it from the record's brief. A tab a handoff retired (`retired`) is no longer its
project's primary and no longer holds its title.

Session state comes from `claude agents --json --all` only, never the CLI's own session files. Standard
library only; nothing here raises on a missing or broken file.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import gk_grants as GR  # noqa: E402
import ij_history as H  # noqa: E402
import ij_naming as N  # noqa: E402

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PRIMARY, SUB = "primary", "sub"
ROLES = (PRIMARY, SUB)
PENDING = "name:"                     # a record whose id `claude --bg` did not print in a form we could read
GH_TIMEOUT_S = 15
SCAN_BYTES = 2_000_000                # how far into a transcript the empty check reads before giving up
TAIL_BYTES = 512_000                  # how much of a transcript's end is read for its last reply
DEFAULTS = {"auto_stop": True, "stop_timeout_minutes": 15, "primary_client": "any",
            "primary_model": "", "primary_effort": "medium", "sub_model": "sonnet", "sub_effort": "medium"}
MAX_TIMEOUT_MINUTES = 24 * 60
CLIENTS = ("any", "terminal", "vscode")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
_MODEL = re.compile(r"[a-z0-9][a-z0-9.\[\]-]{0,63}$")   # an alias or an id; nothing a shell would re-read
_TRUE, _FALSE = ("true", "yes", "on", "1"), ("false", "no", "off", "0")
_SID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{3,79}$")
_PR = re.compile(r"(#?\d{1,7}|https://[A-Za-z0-9.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d{1,7})/?$")
_ISO = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?Z?$")
# Text the CLI writes into a user turn on its own: command echoes and their output, reminders, the notice
# that a background task ended.
_SYSTEM_TEXT = ("<local-command-", "<command-", "<system-reminder>", "<task-notification")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def now_iso_ms() -> str:
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%03dZ" % (int(t * 1000) % 1000)


def epoch(iso) -> float | None:
    """Seconds since 1970 for an ISO time in UTC (`...Z`, with or without a fraction), else None."""
    m = _ISO.match(str(iso or "").strip())
    if not m:
        return None
    import calendar
    return calendar.timegm(time.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")) + float(m.group(2) or 0)


# ---- the store ---------------------------------------------------------------------------------
def roles_path() -> Path:
    return Path(GR.gnomonkit_home()) / "dispatch" / "roles.json"


def _set_aside(path: Path) -> bool:
    """Keep a file that can't be parsed as <name>.bad-<time>, so the next write doesn't lose it."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest, n = path.with_name(f"{path.name}.bad-{stamp}"), 1
    while dest.exists():
        n += 1
        dest = path.with_name(f"{path.name}.bad-{stamp}-{n}")
    try:
        os.replace(path, dest)
    except OSError:
        return False
    print(f"session-dispatch: {path} could not be read, so it was kept as {dest.name} and a new one started",
          file=sys.stderr)
    return True


def _read(strict: bool) -> dict:
    """{key: record}. A missing file is {}. One that can't be parsed is set aside and is {}. One that can't
    be read (locked for a moment on Windows, after retries) is {} for a reader and an OSError for a writer,
    so a write never replaces records it could not see."""
    path = roles_path()
    text = None
    for attempt in range(6):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return {}
        except OSError:
            if attempt == 5:
                if strict:
                    raise
                return {}
            time.sleep(0.05 * (attempt + 1))
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        if not _set_aside(path) and strict:
            raise OSError(f"{path} can't be parsed and couldn't be set aside")
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def load() -> dict:
    """{key: record}. {} when there is no file or it can't be read."""
    return _read(strict=False)


def _save(data: dict) -> None:
    path = roles_path()
    GR._private_dir(str(path.parent))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".roles-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:            # Windows: a reader has the file open for a moment
            if attempt == 5:
                os.unlink(tmp)
                raise
            time.sleep(0.05 * (attempt + 1))


def update(key: str, **fields) -> dict:
    """Merge `fields` into one record (created when absent), re-reading the file just before writing.
    Raises OSError when the file can't be read or written."""
    data = _read(strict=True)
    rec = dict(data.get(key) or {})
    rec.update(fields)
    data[key] = rec
    _save(data)
    return rec


def rekey(old: str, new: str) -> None:
    data = _read(strict=True)
    if old in data and new and new != old:
        data[new] = dict(data.pop(old), **data.get(new, {}))
        _save(data)


def new_record(role: str, project, task: str, name: str | None, parent: str | None, via: str,
               session_id: str | None = None, parent_id: str | None = None) -> dict:
    """`parent_id`: the session id of the session that asked for this one (CLAUDE_CODE_SESSION_ID), which
    the grants read: inside one project, a spawn grant lets that session message and stop it."""
    return {"role": role, "project": str(project), "task": task, "name": name, "parent": parent,
            "parent_id": parent_id, "started": now_iso(), "pr": None, "done": None, "merged": None, "stopped": None,
            "bg": True, "via": via, "sessionId": session_id}


def record_for(store: dict, entry) -> tuple:
    """(key, record) for a session-list entry, or (None, None)."""
    if not isinstance(entry, dict):
        return None, None
    ids = [str(entry.get(k) or "") for k in ("id", "sessionId")]
    for k in ids:
        if k and k in store:
            return k, store[k]
    sid = ids[1]
    if sid:
        for k, r in store.items():
            if r.get("sessionId") == sid:
                return k, r
    name = entry.get("name")
    pending = store.get(PENDING + name) if name else None
    if pending is not None and (entry.get("kind") == "background" or pending.get("client") == "vscode"):
        return PENDING + name, pending
    return None, None


def entry_for(entries, key: str, rec: dict | None):
    """The session-list entry a record belongs to, or None."""
    e = GR.session_entry(entries, key) if not key.startswith(PENDING) else None
    if e is None and rec and rec.get("sessionId"):
        e = GR.session_entry(entries, rec["sessionId"])
    if e is None and key.startswith(PENDING):
        hits = [x for x in entries or () if isinstance(x, dict) and x.get("kind") == "background"
                and x.get("name") == key[len(PENDING):]]
        hits.sort(key=lambda x: x.get("startedAt") or 0)
        e = hits[-1] if hits else None
    return e


def role_of(entry, rec) -> str:
    """The recorded role; a session dispatch didn't start has none, unless its title reads as a sub."""
    if rec and rec.get("role") in ROLES:
        return rec["role"]
    return SUB if (N.read((entry or {}).get("name")) or {}).get("role") == SUB else ""


def _state(entry) -> str:
    from interjector import board as B
    return B._state(entry) if isinstance(entry, dict) else "ended"


def is_archived(rec, state: str) -> bool:
    """A sub that finished (reported done, or was stopped because its pull request merged) and has stopped:
    hidden by default, and its name is free again."""
    return (bool(rec) and rec.get("role") == SUB and bool(rec.get("done") or rec.get("merged"))
            and state == "ended")


# ---- names -------------------------------------------------------------------------------------
def task_part(name) -> str:
    """The `{task}` of a title, read with the naming scheme (`demo-repo:release` -> `release`,
    `sub:release/review` -> `review` by default); a title the scheme doesn't make, as it is."""
    n = str(name or "").strip()
    return (N.read(n) or {}).get("task") or n


def sub_parent_label(name) -> str:
    """The `{parent}` of a sub's title (`sub:release/review` -> `release` by default); "" for anything else."""
    r = N.read(name) or {}
    return (r.get("parent") or "") if r.get("role") == SUB else ""


def sub_name(parent_label: str, task_slug: str, repo: str = "") -> str:
    """A sub's title by the naming scheme's `sub` template."""
    return N.scheme().render(SUB, task_slug, repo=repo, parent=parent_label)


def requester_entry(entries):
    """The session running this command, from the session list (CLAUDE_CODE_SESSION_ID, else CLAUDE_PID)."""
    e = GR.session_entry(entries, os.environ.get("CLAUDE_CODE_SESSION_ID") or "")
    pid = (os.environ.get("CLAUDE_PID") or "").strip()
    if e is None and pid.isdigit():
        hits = [x for x in entries or () if isinstance(x, dict) and str(x.get("pid")) == pid]
        e = hits[0] if len(hits) == 1 else None
    return e


def same_project(a, b) -> bool:
    """Whether two recorded project folders are one project, a worktree path counting as its project."""
    return GR.norm(GR.worktree_home(GR.resolve(a))) == GR.norm(GR.worktree_home(GR.resolve(b)))


def _in_project(entry, rec, project) -> bool:
    """Whether a session belongs to `project`: its recorded project, else its folder is the project, inside
    it, or in the same repository."""
    if rec and rec.get("project"):
        return same_project(rec["project"], project)
    cwd = str((entry or {}).get("cwd") or "")
    if not cwd:
        return False
    c, p = GR.norm(cwd), GR.norm(project)
    if c == p or GR._under(c, p):
        return True
    return os.path.isdir(cwd) and GR.norm(GR.repo_root(cwd)) == GR.norm(GR.repo_root(project))


def name_in_use(entries, store, name: str | None, project, exclude=()) -> dict:
    """Sessions of `project` holding `name`: {"running": [...], "stopped": [...]}, newest first. A session
    elsewhere with the same title doesn't hold it (folders in different places can share a name), nor does
    an archived sub."""
    out = {"running": [], "stopped": []}
    if not name:
        return out
    skip = {str(x) for x in exclude if x}
    for e in entries or ():
        if not isinstance(e, dict) or str(e.get("name") or "").strip() != name:
            continue
        if skip & {str(e.get("id") or ""), str(e.get("sessionId") or "")}:
            continue
        _, rec = record_for(store, e)
        st = _state(e)
        if not _in_project(e, rec, project):
            continue
        if is_archived(rec, st) or (rec or {}).get("retired"):
            continue
        out["stopped" if st == "ended" else "running"].append(e)
    for k in out:
        out[k].sort(key=lambda x: x.get("startedAt") or 0, reverse=True)
    return out


def open_primaries(entries, store, project, exclude=()) -> list:
    """Running sessions recorded as the primary of `project`, except tabs a handoff retired."""
    skip = {str(x) for x in exclude if x}
    out = []
    for e in entries or ():
        key, rec = record_for(store, e)
        if not rec or rec.get("role") != PRIMARY or rec.get("retired") or not _in_project(e, rec, project):
            continue
        if skip & {str(key), str(e.get("id") or ""), str(e.get("sessionId") or "")}:
            continue
        if _state(e) != "ended":
            out.append(e)
    return out


# ---- a primary the user opened -----------------------------------------------------------------
# The first prompt dispatch gives a session it starts or resumes (dispatch.FIRST_PROMPT, RESUME_PROMPT): a
# prompt that begins otherwise was typed by someone. The worktree guard hook reads the same prefixes.
BRIEF_POINTERS = ("Your task brief is in ", "A new brief for this session is in ")


def _prompt_text(rec) -> str:
    return "\n".join(t for t in _texts(rec) if _real_text(t)).strip()


def typed_prompt(entry, after: int = 0) -> bool:
    """True when the session's transcript, past byte `after` (its size when dispatch started or resumed it),
    holds a prompt someone typed: a user prompt after the first one there (dispatch's own) that is not one of
    dispatch's brief pointers. A peer message or a task notice is not a prompt. Reads at most SCAN_BYTES."""
    p = _transcript(entry)
    if not p:
        return False
    read, first = 0, True
    try:
        with open(p, "rb") as fh:
            if after > 0:
                fh.seek(after)
            for raw in fh:
                read += len(raw)
                if read > SCAN_BYTES:
                    return False
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if not is_prompt(rec):
                    continue
                if first:
                    first = False
                elif not _prompt_text(rec).startswith(BRIEF_POINTERS):
                    return True
    except OSError:
        return False
    return False


def is_opened(rec) -> bool:
    """A primary dispatch started that the user has since opened (recorded by sync_opened)."""
    return bool(rec) and rec.get("client") == "tab"


def _opened_now(entries, key: str, rec: dict) -> str | None:
    """Why a background primary dispatch started counts as opened by the user now, else None: the session
    list shows its session id as a session that is not a background one, or someone typed into it."""
    e = entry_for(entries, key, rec)
    sid = str((e or {}).get("sessionId") or rec.get("sessionId") or "")
    if sid and any(isinstance(x, dict) and str(x.get("sessionId") or "") == sid and x.get("kind") != "background"
                   and _state(x) != "ended" for x in entries or ()):
        return "listed as an open tab"
    if e is not None and typed_prompt(e, int(rec.get("typed_after") or 0)):
        return "someone typed into it"
    return None


def sync_opened(entries, store: dict | None = None) -> dict:
    """Record every background primary dispatch started that the user has opened (_opened_now): client
    "tab", bg false, opened <time>. From then on it takes no background slot, a handoff retires it as a tab
    (nothing is stopped) and the stop-when-idle watcher leaves it running. Returns the store, re-read when
    anything changed. Never raises."""
    store = load() if store is None else store
    changed = False
    for key, rec in list(store.items()):
        if rec.get("role") != PRIMARY or is_opened(rec) or rec.get("client") == "vscode" or rec.get("retired"):
            continue
        try:
            why = _opened_now(entries, key, rec)
            if why:
                update(key, client="tab", bg=False, opened=now_iso(), opened_how=why)
                changed = True
        except Exception:  # noqa: BLE001 - bookkeeping never fails the command around it
            continue
    return load() if changed else store


def unattended(entries, store: dict, exclude=()) -> list:
    """The running background sessions the cap counts: ones dispatch started (a record), not a primary the
    user opened, never the session asking (requester_entry) or one in `exclude` (ids about to stop). A
    background session dispatch didn't start is the user's own and doesn't count."""
    me = requester_entry(entries)
    skip = {str(x) for x in exclude if x} | ({str(me.get(k) or "") for k in ("id", "sessionId")} - {""} if me else set())
    out = []
    for e in entries or ():
        if not isinstance(e, dict) or e.get("kind") != "background" or _state(e) == "ended":
            continue
        if skip & {str(e.get("id") or ""), str(e.get("sessionId") or "")}:
            continue
        _, rec = record_for(store, e)
        if rec is None or is_opened(rec):
            continue
        out.append(e)
    return out


def started_id(output: str, name: str | None, before, fetch=None) -> tuple:
    """(id, sessionId) of a session `claude --bg` just started. Its output is the id; failing that, an id
    the session list shows that appears in the output, else the newest background session with this title
    that wasn't listed before. (None, None) when it can't be told."""
    out = (output or "").strip()
    if _SID.match(out):
        return out, None
    entries = []
    if fetch is not None:
        entries, _error = fetch()
    tokens = set(re.findall(r"[A-Za-z0-9_-]{4,80}", out))
    for e in entries:
        if str(e.get("id") or "") in tokens or str(e.get("sessionId") or "") in tokens:
            return str(e.get("id") or e.get("sessionId")), e.get("sessionId")
    known = {str(x.get("id") or x.get("sessionId") or "") for x in before or ()}
    hits = [e for e in entries if e.get("kind") == "background" and name and e.get("name") == name
            and str(e.get("id") or e.get("sessionId") or "") not in known]
    hits.sort(key=lambda e: e.get("startedAt") or 0)
    if hits:
        return str(hits[-1].get("id") or hits[-1].get("sessionId")), hits[-1].get("sessionId")
    return None, None


# ---- settings ----------------------------------------------------------------------------------
personal_profile_path = N.personal_profile_path


def _profile_table() -> dict:
    return N.profile_table("dispatch")


def _valid(name: str, v):
    if name == "auto_stop":
        return v if isinstance(v, bool) else None
    if name == "stop_timeout_minutes":
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
        return min(v, MAX_TIMEOUT_MINUTES) if ok else None
    if name == "primary_client":
        return v.lower() if isinstance(v, str) and v.lower() in CLIENTS else None
    if name.endswith("_model"):
        return valid_model(v)
    if name.endswith("_effort"):
        return valid_effort(v)
    return None


def valid_model(v):
    """A model alias or id, lower-cased; "" for the user's own default; None when it doesn't fit."""
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    return s if s == "" or _MODEL.match(s) else None


def valid_effort(v):
    """An effort level; "" for the user's own default; None when it doesn't fit."""
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    return s if s == "" or s in EFFORTS else None


def _from_text(name: str, raw: str):
    s = raw.strip().lower()
    if name == "auto_stop":
        return True if s in _TRUE else False if s in _FALSE else None
    if name == "stop_timeout_minutes":
        try:
            return _valid(name, float(s))
        except ValueError:
            return None
    return _valid(name, s)


def setting(name: str):
    """A dispatch setting: GNOMONKIT_DISPATCH_<NAME>, else the profile's [dispatch] table, else the default.
    A value that doesn't fit is ignored."""
    raw = (os.environ.get("GNOMONKIT_DISPATCH_" + name.upper()) or "").strip()
    if raw:
        v = _from_text(name, raw)
        if v is not None:
            return v
    v = _valid(name, _profile_table().get(name))
    return DEFAULTS[name] if v is None else v


# ---- transcripts -------------------------------------------------------------------------------
def config_dir() -> Path:
    raw = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return Path(os.path.expanduser(raw)) if raw else Path.home() / ".claude"


def project_slug(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd or ""))


def transcript_path(session_id, cwd=None) -> Path | None:
    """<config>/projects/<slug of cwd>/<session id>.jsonl, else that file in any project folder there."""
    sid = str(session_id or "").strip()
    if not _SID.match(sid):
        return None
    base = config_dir() / "projects"
    if cwd:
        p = base / project_slug(str(cwd)) / f"{sid}.jsonl"
        if p.is_file():
            return p
    try:
        for d in base.iterdir():
            p = d / f"{sid}.jsonl"
            if p.is_file():
                return p
    except OSError:
        pass
    return None


def _real_text(t) -> bool:
    t = str(t or "").strip()
    return bool(t) and not t.startswith(_SYSTEM_TEXT)


def is_prompt(rec) -> bool:
    """A transcript line that is something the user typed (not a tool result, not the CLI's own text)."""
    if not isinstance(rec, dict) or rec.get("type") != "user" or rec.get("isMeta"):
        return False
    c = (rec.get("message") or {}).get("content") if isinstance(rec.get("message"), dict) else None
    if isinstance(c, str):
        return _real_text(c)
    if isinstance(c, list):
        return any(isinstance(b, dict) and b.get("type") == "text" and _real_text(b.get("text")) for b in c)
    return False


def has_conversation(path) -> bool | None:
    """True when the transcript holds a user prompt or a reply, False when it holds neither, None when it
    can't be told (unreadable, or no answer in the first SCAN_BYTES)."""
    read = 0
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                read += len(raw)
                if read > SCAN_BYTES:
                    return None
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(rec, dict) and (rec.get("type") == "assistant" or is_prompt(rec)):
                    return True
    except OSError:
        return None
    return False


def is_empty(entry, state: str) -> bool:
    """An idle interactive session with no transcript, or one holding no prompt and no reply. Busy and
    waiting sessions are never empty here, nor background ones (they start with a prompt, so one whose
    transcript isn't found is still real work), nor one whose session id the list doesn't give."""
    if state != "idle" or not isinstance(entry, dict) or entry.get("kind") == "background":
        return False
    sid = entry.get("sessionId")
    if not sid:
        return False
    p = transcript_path(sid, entry.get("cwd"))
    return p is None or has_conversation(p) is False


def _texts(rec) -> list:
    c = (rec.get("message") or {}).get("content") if isinstance(rec.get("message"), dict) else None
    if isinstance(c, str):
        return [c] if c.strip() else []
    return [b["text"] for b in c or () if isinstance(b, dict) and b.get("type") == "text"
            and isinstance(b.get("text"), str) and b["text"].strip()] if isinstance(c, list) else []


def _origin(rec) -> str | None:
    o = rec.get("origin") if isinstance(rec, dict) else None
    return o.get("kind") if isinstance(o, dict) else None


def is_new_work(rec) -> bool:
    """A user turn that brings the session work: something typed, or a message another session sent it
    (SendMessage, which the CLI records as a meta message from a peer)."""
    if not isinstance(rec, dict) or rec.get("type") != "user":
        return False
    if _origin(rec) == "peer":
        return any(_real_text(t) for t in _texts(rec))
    return is_prompt(rec)


def is_turn_input(rec) -> bool:
    """Anything that starts a turn: new work, or the notice that a background task it started has ended."""
    if is_new_work(rec):
        return True
    return (isinstance(rec, dict) and rec.get("type") == "user"
            and (_origin(rec) == "task-notification"
                 or any(t.lstrip().startswith("<task-notification") for t in _texts(rec))))


def _tail(path) -> list:
    """The records in the transcript's last TAIL_BYTES, oldest first; [] when it can't be read."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            start = max(0, size - TAIL_BYTES)
            fh.seek(start)
            lines = fh.read().split(b"\n")
    except OSError:
        return []
    if start:
        lines = lines[1:]                     # the first line read may be cut
    out = []
    for raw in lines:
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _reply_and_after(records) -> tuple:
    """(text of the last assistant message or None, the turn inputs that came after it)."""
    msg_id, parts, after = None, [], []
    for rec in reversed(records):
        if rec.get("type") == "assistant":
            mid = (rec.get("message") or {}).get("id") if isinstance(rec.get("message"), dict) else None
            texts = _texts(rec)
            if parts:
                if mid is None or mid != msg_id:
                    break
                parts[:0] = texts
            elif texts:
                msg_id, parts = mid, texts
            continue
        if parts:
            break
        if is_turn_input(rec):
            after.append(rec)
    return ("\n\n".join(parts) if parts else None), after


def last_reply(path) -> str | None:
    """The text of the transcript's last assistant message, or None (none yet, or something reached the
    session after it)."""
    text, after = _reply_and_after(_tail(path))
    return None if after else text


def _transcript(entry) -> Path | None:
    return transcript_path((entry or {}).get("sessionId"), (entry or {}).get("cwd"))


def asks_user(entry) -> str | None:
    """What the session's last reply asks the user, or None."""
    p = _transcript(entry)
    text = last_reply(p) if p else None
    return H.asks_user(text) if text else None


def has_reply(entry) -> bool:
    p = _transcript(entry)
    return bool(p and last_reply(p))


def pending_input(entry) -> bool:
    """True when something reached the session after its last reply (a prompt, a message, a task notice):
    it has a turn to take, whatever the list says right now."""
    p = _transcript(entry)
    return bool(p and _reply_and_after(_tail(p))[1])


def new_work_since(entry, since) -> bool:
    """True when work reached the session after `since` (an ISO time): a prompt or a message from another
    session timestamped later, or one still waiting after its last reply."""
    p = _transcript(entry)
    if not p:
        return False
    records = _tail(p)
    if any(is_new_work(r) for r in _reply_and_after(records)[1]):
        return True
    t0 = epoch(since)
    if t0 is None:
        return False
    for r in records:
        t = epoch(r.get("timestamp")) if is_new_work(r) else None
        if t is not None and t > t0:
            return True
    return False


def transcript_size(entry) -> int:
    """The size of the session's transcript in bytes, 0 when there is none."""
    p = _transcript(entry)
    try:
        return p.stat().st_size if p else 0
    except OSError:
        return 0


# ---- pull requests -----------------------------------------------------------------------------
def normalise_pr(raw) -> str | None:
    s = str(raw or "").strip()
    m = _PR.match(s)
    return m.group(1).lstrip("#") if m else None


def _gh_cmd() -> list | None:
    raw = os.environ.get("GNOMONKIT_GH_CMD")
    if raw:
        try:
            cmd = json.loads(raw)
        except ValueError:
            return None
        return [str(c) for c in cmd] if isinstance(cmd, list) and cmd else None
    exe = shutil.which("gh")
    return [exe] if exe else None


def _gh_pr(pr: str, field: str, cwd=None) -> tuple:
    """(value, error): one field of `gh pr view <pr> --json <field>`, run in `cwd` (where a bare number
    means that folder's repository's pull request)."""
    pr = normalise_pr(pr)
    if not pr:
        return None, "not a pull request number or URL"
    cmd = _gh_cmd()
    if not cmd:
        return None, "gh was not found on PATH"
    try:
        r = subprocess.run([*cmd, "pr", "view", pr, "--json", field], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=GH_TIMEOUT_S, creationflags=NO_WINDOW,
                           cwd=str(cwd) if cwd and os.path.isdir(str(cwd)) else None)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"gh could not run ({e.__class__.__name__})"
    if r.returncode != 0:
        return None, f"gh pr view exited {r.returncode}"
    try:
        value = json.loads(r.stdout or "{}").get(field)
    except (ValueError, AttributeError):
        return None, "gh printed something that is not JSON"
    return (value, None) if isinstance(value, str) and value.strip() else (None, f"gh gave no {field}")


def pr_state(pr: str, cwd=None) -> tuple:
    """(state, error): state is gh's word (OPEN, MERGED, CLOSED) or None."""
    state, err = _gh_pr(pr, "state", cwd)
    return (state.upper(), None) if state else (None, err)


def pr_url(pr: str, cwd=None) -> str | None:
    """The URL of the pull request a number names in `cwd`'s repository (a URL is kept as it is), so a
    later check reads the same pull request from any folder. None when gh can't say."""
    pr = normalise_pr(pr)
    if not pr or pr.startswith("https://"):
        return pr
    url, _err = _gh_pr(pr, "url", cwd)
    return normalise_pr(url) if url and normalise_pr(url) and url.startswith("https://") else None


# ---- finished subs -----------------------------------------------------------------------------
def finished(entry, rec, state: str, check_pr=pr_state) -> tuple:
    """(True, why) when this is a finished sub dispatch may stop, else (False, why not)."""
    if not rec:
        return False, "not started by dispatch"
    if rec.get("role") != SUB:
        return False, "a primary" if rec.get("role") == PRIMARY else "not a sub"
    if not rec.get("bg"):
        return False, "not started with --bg"
    if entry is None:
        return False, "not in the session list"
    if entry.get("kind") != "background":
        return False, "an interactive session"
    if state == "ended":
        return False, "already stopped"
    if state == "busy":
        return False, "busy"
    if state == "waiting" or entry.get("waitingFor"):
        return False, "waiting on the user"
    if state != "idle":
        return False, f"state {state}"
    if pending_input(entry):
        return False, "something reached it after its last reply, so it has a turn to take"
    asked = asks_user(entry)
    if asked:
        return False, "its last message asks the user: " + " ".join(asked.split())[:100]
    if rec.get("done"):
        return True, "reported done and is idle"
    if rec.get("pr"):
        # A URL names its repository; a bare number is read where the sub recorded it.
        st, err = check_pr(rec["pr"], rec.get("pr_dir") or entry.get("cwd") or rec.get("project"))
        if st == "MERGED":
            return True, f"its pull request {rec['pr']} merged"
        return False, (f"its pull request {rec['pr']} is {st.lower()}" if st
                       else f"its pull request's state is unknown ({err})")
    return False, "not reported done"


def pending_subs(store: dict) -> dict:
    """Records that could become a finished sub: subs started with --bg and not stopped by dispatch."""
    return {k: r for k, r in store.items() if r.get("role") == SUB and r.get("bg") and not r.get("stopped")}


# ---- grouping ----------------------------------------------------------------------------------
def project_label(cwd: str, rec=None, cache: dict | None = None) -> str:
    """What a session is grouped under: the main repository's name of the recorded project, else of its
    folder (a linked worktree groups with its repository), else the folder's name."""
    cwd = str((rec or {}).get("project") or cwd or "")
    if cache is not None and cwd in cache:
        return cache[cwd]
    label = os.path.basename(os.path.normpath(cwd)) if cwd else ""
    if cwd and os.path.isdir(cwd):
        ident = N.git_identity(cwd)
        if ident:
            label = ident["main"]
    if cache is not None:
        cache[cwd] = label
    return label


def arrange(rows: list, key) -> list:
    """Rows grouped by row["project"]: the group with the most urgent row (by `key`) first; inside a group,
    the primary, then sessions without a role, then subs, each sub straight under its parent (by title).
    Sets row["depth"] (0, or how far a sub sits under its parent)."""
    groups: dict = {}
    for r in rows:
        groups.setdefault(str(r.get("project") or "").casefold(), []).append(r)
    order = sorted(groups.items(), key=lambda kv: (min(key(r) for r in kv[1]), kv[0]))
    out: list = []
    for _, g in order:
        out += _tree(g, key)
    return out


def _tree(rows: list, key) -> list:
    names = {r.get("name") for r in rows if r.get("name")}
    kids: dict = {}
    top = []
    for r in rows:
        p = r.get("parent")
        if r.get("role") == SUB and p and p in names and p != r.get("name"):
            kids.setdefault(p, []).append(r)
        else:
            top.append(r)
    rank = {PRIMARY: 0, SUB: 2}
    top.sort(key=lambda r: (rank.get(r.get("role"), 1), key(r)))
    out, seen = [], set()

    def walk(r, depth):
        if id(r) in seen:
            return
        seen.add(id(r))
        r["depth"] = depth
        out.append(r)
        for k in sorted(kids.get(r.get("name"), ()), key=key):
            walk(k, depth + 1)

    for r in top:
        walk(r, 1 if r.get("role") == SUB else 0)
    for r in rows:                          # a parent loop: still listed, once
        walk(r, 1)
    return out
