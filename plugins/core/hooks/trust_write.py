"""Mark repositories trusted in Claude Code's own file (~/.claude.json), changing nothing else.

The one writer of that file in Gnomonkit, used by `gnomonkit trust sync` (in a terminal) and by the
trust_autodiscover SessionStart hook. For each repository it sets projects["<repository root>"]
.hasTrustDialogAccepted = true, and nothing else:

  1. The file must be one Gnomonkit recognises: UTF-8 JSON, an object whose `projects` is an object, with no
     key repeated where the edit goes. Anything else is left as it is.
  2. The edit is made in the text, not by re-serialising: an existing value becomes `true`, or one member
     is inserted, laid out like its neighbours. Every other byte stays as it was. The edited text is parsed
     and must equal the old file with exactly those keys set, or nothing is written.
  2a. Each repository is checked again first, against the levels as they are now: its path must still lead
     to itself (a link or junction made there since the scan is refused, and paths are never resolved
     again after that check), it must still be a repository under a root at the `trusted` level (with
     `auto` on, for the session-start hook), and its origin's owner must still be listed.
  3. A backup of the file as read goes to ~/.gnomonkit/trust/backups/ (0600; the last BACKUPS kept).
  4. The new text goes to a temp file in the same folder; right before each try at the swap the file is
     read again, and if it changed since step 1, nothing is written. Then os.replace, so a reader sees the
     old file or the new one, never half of one.
  5. The file is read back. Claude Code rewrites it while it runs and may write back a copy without the
     new key; a key that didn't stick is tried once more from step 1.
  6. Every change, refusal, abort and failure goes to ~/.gnomonkit/trust/audit.jsonl
     (gk_grants.audit_append).

One writer at a time: a lock file in ~/.gnomonkit/trust/ holding a token of its own. A stale one (older
than LOCK_STALE_S) is moved aside under a name only this process uses and removed only if it is still the
lock that was judged stale. Session-start state (which roots were scanned, which repositories Gnomonkit
trusted) lives in ~/.gnomonkit/trust/state.json, so a key Claude Code lost is put back on the next start.

Core plugin only: session-dispatch reads trust (gk_trust) but never writes it. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import tempfile
import time
from json.decoder import scanstring

import gk_grants as G
import gk_trust as T

BACKUPS = 10
LOCK_STALE_S = 60
REPLACE_TRIES = 5
REPLACE_RETRY = os.name == "nt"     # only Windows refuses a replace while another process has the file open
STATE_VERSION = 1
_DEC = json.JSONDecoder()
_WS = " \t\r\n"


class WriteError(Exception):
    """Nothing was written. kind: format (not a file Gnomonkit recognises), changed (it changed between the
    read and the write), busy (another Gnomonkit writer holds the lock), levels (the folder-trust levels
    can't be read), io."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


# ---- the edit, in the text ---------------------------------------------------------------------
class _Member:
    __slots__ = ("key", "lead", "key_start", "key_end", "value_start", "value_end")

    def __init__(self, key, lead, key_start, key_end, value_start, value_end):
        self.key, self.lead, self.key_start, self.key_end = key, lead, key_start, key_end
        self.value_start, self.value_end = value_start, value_end


class _Obj:
    __slots__ = ("open", "close", "members", "by_key")

    def __init__(self, open_):
        self.open, self.close, self.members, self.by_key = open_, -1, [], {}

    def get(self, key):
        return self.by_key.get(key)


def _ws(s: str, i: int) -> int:
    n = len(s)
    while i < n and s[i] in _WS:
        i += 1
    return i


def _scan_object(s: str, i: int) -> _Obj:
    """The members of the object starting at s[i], with their spans. A repeated key is a ValueError: which
    one a reader keeps is not something to guess at."""
    if i >= len(s) or s[i] != "{":
        raise ValueError("expected an object")
    obj = _Obj(i)
    lead_start, j = i + 1, _ws(s, i + 1)
    if j < len(s) and s[j] == "}":
        obj.close = j
        return obj
    while True:
        if j >= len(s) or s[j] != '"':
            raise ValueError("expected a key")
        key, k = scanstring(s, j + 1)
        if key in obj.by_key:
            raise ValueError("a key appears twice")
        key_end = k
        k = _ws(s, k)
        if k >= len(s) or s[k] != ":":
            raise ValueError("expected ':'")
        vs = _ws(s, k + 1)
        _, ve = _DEC.raw_decode(s, vs)
        m = _Member(key, s[lead_start:j], j, key_end, vs, ve)
        obj.members.append(m)
        obj.by_key[key] = m
        k = _ws(s, ve)
        if k < len(s) and s[k] == ",":
            lead_start, j = k + 1, _ws(s, k + 1)
            continue
        if k < len(s) and s[k] == "}":
            obj.close = k
            return obj
        raise ValueError("expected ',' or '}'")


def _line_indent(s: str, i: int) -> str:
    start = s.rfind("\n", 0, i) + 1
    j = start
    while j < len(s) and s[j] in " \t":
        j += 1
    return s[start:j]


class _Layout:
    """How the file is laid out, so inserted text looks like its neighbours."""

    def __init__(self, s: str, top: _Obj):
        self.nl = "\r\n" if "\r\n" in s else "\n"
        first = top.members[0].lead if top.members else ""
        self.unit = first.rsplit("\n", 1)[-1] if "\n" in first else ""
        self.pretty = bool(self.unit)
        m = top.members[0] if top.members else None
        self.sep = s[m.key_end:m.value_start] if m else (": " if self.pretty else ":")

    def member_indent(self, s: str, obj: _Obj) -> str:
        if obj.members and "\n" in obj.members[-1].lead:
            return obj.members[-1].lead.rsplit("\n", 1)[-1]
        return _line_indent(s, obj.open) + self.unit

    def members_into(self, s: str, obj: _Obj, members: list) -> tuple:
        """(start, end, text): add `members` ([(key_json, value_text_for(indent))]) at the end of `obj`."""
        indent = self.member_indent(s, obj)
        texts = [k + self.sep + v(indent) for k, v in members]
        if obj.members:
            last = obj.members[-1]
            lead = last.lead if last.lead.strip() == "" and last.lead else (self.nl + indent if self.pretty else "")
            return last.value_end, last.value_end, "".join("," + lead + t for t in texts)
        if self.pretty:
            close = _line_indent(s, obj.open)
            body = ("," + self.nl).join(indent + t for t in texts)
            return obj.open, obj.close + 1, "{" + self.nl + body + self.nl + close + "}"
        return obj.open, obj.close + 1, "{" + ",".join(texts) + "}"

    def entry_value(self, indent: str) -> str:
        key = json.dumps(T.TRUST_KEY)
        if self.pretty:
            return "{" + self.nl + indent + self.unit + key + self.sep + "true" + self.nl + indent + "}"
        return "{" + key + self.sep + "true}"


_UNSET = object()


def _style(projects: dict):
    """On Windows, (backslashes, upper-case drive letter) as most of the file's own drive-letter keys have
    them; None when there are none to follow, or elsewhere."""
    if os.name != "nt":
        return None
    keys = [k for k in projects if re.match(r"^[A-Za-z]:[\\/]", k)]
    if not keys:
        return None
    return sum("\\" in k for k in keys) * 2 > len(keys), sum(k[0].isupper() for k in keys) * 2 >= len(keys)


def spelling(repo, projects: dict, style=_UNSET) -> str:
    """The key a new entry gets: the repository root as the scan found it (resolved then, not again here),
    with forward slashes; on Windows, separators and drive-letter case as most of the file's own
    drive-letter keys have them (`style`, from _style, to work that out once for many repositories)."""
    p = _as_found(repo)
    st = _style(projects) if style is _UNSET else style
    if st:
        back, upper = st
        if back:
            p = p.replace("/", "\\")
        p = (p[0].upper() if upper else p[0].lower()) + p[1:]
    return p


def edit_text(text: str, repos: list, projects: dict, index: dict | None = None) -> tuple:
    """(new text, [(repo, key, change)]) with hasTrustDialogAccepted = true on each repository's entry, every
    spelling of it that is there, or a new entry when there is none. `repos` are resolved paths, matched as
    they are (gk_trust.entry_keys resolved=True); `index` is gk_trust.key_index(projects). ValueError when
    the layout can't be edited safely."""
    top = _scan_object(text, _ws(text, 0))
    if text[top.close + 1:].strip(_WS):
        raise ValueError("text after the object")
    pm = top.get("projects")
    if pm is None:
        raise ValueError("no projects object")
    pobj = _scan_object(text, pm.value_start)
    lay = _Layout(text, top)
    edits, changes, new = [], [], []
    tkey = json.dumps(T.TRUST_KEY)
    idx = index if index is not None else T.key_index(projects)
    style = _style(projects)
    for repo in repos:
        keys = T.entry_keys(projects, repo, idx, resolved=True)
        if not keys:
            key = spelling(repo, projects, style)
            new.append((json.dumps(key, ensure_ascii=False), lay.entry_value))
            changes.append((repo, key, "added entry"))
            continue
        for key in keys:
            m = pobj.get(key)
            if m is None or not isinstance(projects.get(key), dict):
                raise ValueError("an entry for the repository is not an object")
            if projects[key].get(T.TRUST_KEY) is True:
                continue
            eobj = _scan_object(text, m.value_start)
            tm = eobj.get(T.TRUST_KEY)
            if tm is not None:
                edits.append((tm.value_start, tm.value_end, "true"))
                changes.append((repo, key, "set key"))
            else:
                edits.append(lay.members_into(text, eobj, [(tkey, lambda _indent: "true")]))
                changes.append((repo, key, "added key"))
    if new:
        edits.append(lay.members_into(text, pobj, new))
    # Joined in one pass: splicing edit by edit copies the whole file each time, which adds up with
    # thousands of entries.
    parts, pos = [], 0
    for start, end, piece in sorted(edits, key=lambda e: (e[0], e[1])):
        parts += [text[pos:start], piece]
        pos = end
    parts.append(text[pos:])
    return "".join(parts), changes


def expected_after(text: str, changes: list) -> dict:
    data = json.loads(text)
    for _repo, key, _change in changes:
        entry = data["projects"].setdefault(key, {})
        entry[T.TRUST_KEY] = True
    return data


# ---- files ---------------------------------------------------------------------------------------
def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _private_file(path: str, data: bytes) -> None:
    G._private_dir(os.path.dirname(path))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def backup_dir() -> str:
    return os.path.join(T.trust_home(), "backups")


def backup(raw: bytes) -> str:
    """Keep a copy of the file as read; the newest BACKUPS are kept."""
    folder = backup_dir()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    n = 0
    while True:
        path = os.path.join(folder, "claude.json.%s-%d-%d.bak" % (stamp, os.getpid(), n))
        try:
            _private_file(path, raw)
            break
        except FileExistsError:
            n += 1
    try:
        old = sorted(f for f in os.listdir(folder) if f.startswith("claude.json.") and f.endswith(".bak"))
        for f in old[:-BACKUPS]:
            os.unlink(os.path.join(folder, f))
    except OSError:
        pass
    return path


def _swap(path: str, old_raw: bytes, new_raw: bytes, before_swap=None) -> None:
    """Write `new_raw` beside `path` and swap it in, unless the file no longer holds `old_raw`."""
    folder = os.path.dirname(path)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    fd, tmp = tempfile.mkstemp(prefix=".claude.json.", suffix=".gnomonkit.tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(new_raw)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name != "nt":
            os.chmod(tmp, mode)
        if before_swap is not None:
            before_swap()
        for attempt in range(REPLACE_TRIES):
            # Read again right before every try: a refused replace means another process had the file open,
            # and it may have written it in the meantime.
            if _read(path) != old_raw:
                raise WriteError("changed", "%s changed between Gnomonkit's read and its write, so nothing was "
                                            "written (Claude Code rewrites it while it runs; try again)" % path)
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                # Windows refuses to replace a file another process holds open for a moment.
                if not REPLACE_RETRY or attempt == REPLACE_TRIES - 1:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _Lock:
    """One writer at a time: write.lock, created exclusively, holding a token of this process's own."""

    def __init__(self):
        self.path = os.path.join(T.trust_home(), "write.lock")
        self.token = ("%d-%s" % (os.getpid(), secrets.token_hex(8))).encode()
        self.held = False

    def _create(self) -> bool:
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "wb") as fh:
            fh.write(self.token)
        self.held = True
        return True

    def _take_stale(self) -> bool:
        """Move a stale lock out of the way. Renaming it to a name only this process uses succeeds for one
        process at most; the moved file is then compared with the lock that was judged stale, and when it is
        a fresh lock another process took in between, it is put back. True: try to create the lock again."""
        try:
            if time.time() - os.path.getmtime(self.path) <= LOCK_STALE_S:
                return False
            judged = _read(self.path)
        except OSError:
            return True                                   # gone in the meantime
        aside = "%s.%s.stale" % (self.path, self.token.decode())
        try:
            os.rename(self.path, aside)
        except OSError:
            return True                                   # another process moved it first
        try:
            if _read(aside) == judged:
                return True
            try:
                os.link(aside, self.path)                 # someone else's fresh lock: put it back
            except OSError:
                pass
            return False
        except OSError:
            return False
        finally:
            try:
                os.unlink(aside)
            except OSError:
                pass

    def __enter__(self):
        G._private_dir(os.path.dirname(self.path))
        for _ in range(3):
            if self._create():
                return self
            if not self._take_stale():
                break
        raise WriteError("busy", "another Gnomonkit process is changing folder trust right now; nothing was written")

    def __exit__(self, *_exc):
        if self.held:
            try:
                if _read(self.path) == self.token:        # never remove a lock that is not this one
                    os.unlink(self.path)
            except OSError:
                pass
        return False


def _audit(record: dict) -> None:
    G.audit_append(record, G.audit_path("trust"))


# ---- the one entry point -------------------------------------------------------------------------
def _as_found(p) -> str:
    """A path as given, with forward slashes and no trailing one; never resolved (see Item)."""
    s = str(p).strip().replace("\\", "/")
    if len(s) > 1 and s.endswith("/") and not re.match(r"^[A-Za-z]:/$", s):
        s = s.rstrip("/")
    return s


class Item:
    """A repository to trust, as the scan found it: its root, already resolved by the scan and never resolved
    again here (set_trusted checks that it still leads to itself), the [[trust]] root that allowed it, why,
    and (for the audit log) what is trusting it when that differs from set_trusted's `by`."""

    __slots__ = ("repo", "root", "reason", "by")

    def __init__(self, repo, root="", reason="", by=None):
        self.repo, self.root, self.reason, self.by = _as_found(repo), root, reason, by


def still_allowed(it: Item, levels, by: str) -> str | None:
    """None when `it` may be trusted now, else why not. Runs inside the lock right before the edit: between
    the scan and the write (a person reading the list and typing the word, say) the folder could have been
    swapped for a link to somewhere else, or the levels or the remote changed."""
    now = G.resolve(it.repo)
    if G.norm(now) != T.key_norm(it.repo):
        return "the folder no longer leads to itself (a link or junction was made there after the scan)"
    if not T.is_repo(now):
        return "it is no longer a git repository"
    gov = T.governing(now, levels, resolved=True)
    if gov is None or gov.level != "trusted":
        return "it is not under a root at the trusted level now (%s)" % ("no root" if gov is None
                                                                         else "level " + gov.level)
    if (it.by or by) in ("auto", "reapply") and not gov.auto:
        return "its root has auto off now"
    ok, why = T.qualifies(now, gov)
    if not ok:
        return why
    it.root, it.reason = gov.root, why
    return None


class Result:
    """What set_trusted did: changed (audit records), already (trusted before), failed (key didn't stick),
    refused (no longer allowed, untouched), deferred (not reached before the deadline, untouched)."""

    __slots__ = ("path", "changed", "already", "failed", "refused", "deferred", "backups", "attempts")

    def __init__(self, path):
        self.path, self.changed, self.already, self.failed = path, [], [], []
        self.refused, self.deferred = [], []
        self.backups, self.attempts = [], 0


def set_trusted(items: list, by: str, path: str | None = None, retries: int = 1, before_swap=None,
                after_swap=None, deadline: float | None = None) -> Result:
    """Trust each Item's repository in Claude Code's file, by the steps in this module's docstring.
    `by` (sync, auto, reapply) goes to the audit log. Raises WriteError when nothing could be written;
    otherwise Result.failed lists the keys that did not stick after `retries` more tries, Result.refused the
    repositories the check right before the edit turned down, and Result.deferred those not reached before
    `deadline` (time.monotonic()), left for another time. `before_swap` and `after_swap` are for tests (a
    concurrent writer)."""
    path = os.path.realpath(path or T.claude_json_path())
    res = Result(path)
    pending, seen = [], set()
    for it in items:
        k = T.key_norm(it.repo)
        if k not in seen:
            seen.add(k)
            pending.append(it)
    with _Lock():
        levels = T.load_levels()
        if not levels.ok:
            _audit({"event": "refused", "by": by, "file": path, "why": "folder-trust levels: %s" % levels.status})
            raise WriteError("levels", "the folder-trust levels in %s are %s, so nothing was trusted"
                             % (levels.path, levels.status))
        for attempt in range(1, retries + 2):
            res.attempts = attempt
            try:
                raw = _read(path)
            except FileNotFoundError:
                raise WriteError("format", "there is no %s yet; nothing was written" % path) from None
            except OSError as e:
                raise WriteError("io", "%s can't be read (%s); nothing was written" % (path, type(e).__name__)) from None
            data, problem = T.parse_claude_config(raw)
            if problem:
                _audit({"event": "refused", "by": by, "file": path, "why": problem})
                raise WriteError("format", "%s is left as it is: %s, which is not a format Gnomonkit recognises"
                                 % (path, problem))
            projects = data["projects"]
            index = T.key_index(projects)
            done = [it for it in pending if T.is_trusted(projects, it.repo, index, resolved=True)]
            if attempt == 1:
                res.already += [it.repo for it in done]
            allowed = []
            for it in pending:
                if it in done:
                    continue
                if deadline is not None and time.monotonic() > deadline:
                    res.deferred.append(it.repo)
                    continue
                why = still_allowed(it, levels, by)
                if why:
                    res.refused.append((it.repo, why))
                    _audit({"event": "refused", "by": it.by or by, "file": path, "repo": it.repo, "why": why})
                    continue
                allowed.append(it)
            pending = allowed
            if not pending:
                break
            text = raw.decode("utf-8")
            try:
                new_text, changes = edit_text(text, [it.repo for it in pending], projects, index)
                ok = json.loads(new_text) == expected_after(text, changes)
            except ValueError as e:
                _audit({"event": "refused", "by": by, "file": path, "why": "layout: %s" % e})
                raise WriteError("format", "%s is left as it is: Gnomonkit can't edit its layout safely (%s)"
                                 % (path, e)) from None
            if not ok:
                _audit({"event": "refused", "by": by, "file": path, "why": "the edit did not check out"})
                raise WriteError("format", "%s is left as it is: the edited file did not match the planned change"
                                 % path)
            saved = backup(raw)
            res.backups.append(saved)
            try:
                _swap(path, raw, new_text.encode("utf-8"), before_swap if attempt == 1 else None)
            except WriteError as e:
                _audit({"event": "aborted", "by": by, "file": path, "why": e.kind,
                        "repos": [it.repo for it in pending]})
                raise
            except OSError as e:
                _audit({"event": "aborted", "by": by, "file": path, "why": type(e).__name__})
                raise WriteError("io", "%s could not be replaced (%s); nothing was written" % (path, type(e).__name__)) \
                    from None
            if after_swap is not None:
                after_swap(attempt)
            try:
                back, problem = T.parse_claude_config(_read(path))
            except OSError:
                back, problem = None, "unreadable"
            back_index = T.key_index(back["projects"]) if problem is None else {}
            stuck = [it for it in pending
                     if problem is None and T.is_trusted(back["projects"], it.repo, back_index, resolved=True)]
            by_repo = {}
            for repo, key, change in changes:
                by_repo.setdefault(repo, []).append((key, change))
            for it in stuck:
                for key, change in by_repo.get(it.repo, []):
                    rec = {"repo": it.repo, "key": key, "change": change, "root": it.root, "reason": it.reason,
                           "attempt": attempt, "by": it.by or by}
                    res.changed.append(rec)
                    _audit(dict(rec, event="reapplied" if rec["by"] == "reapply" else "trusted", file=path,
                                backup=saved))
            lost = [it for it in pending if it not in stuck]
            for it in lost:
                _audit({"event": "lost", "by": it.by or by, "file": path, "repo": it.repo, "attempt": attempt,
                        "why": "the key was not there when read back (the file was rewritten after the write)"})
            pending = lost
            if not pending:
                break
        for it in pending:
            res.failed.append((it.repo, "the key did not stick: %s was rewritten after each write" % path))
            _audit({"event": "not-stuck", "by": it.by or by, "file": path, "repo": it.repo})
    return res


# ---- what Gnomonkit has trusted, for the session-start hook ----------------------------------------
def state_path() -> str:
    return os.path.join(T.trust_home(), "state.json")


def read_state() -> dict:
    try:
        with open(state_path(), encoding="utf-8") as fh:
            st = json.load(fh)
        if isinstance(st, dict) and st.get("version") == STATE_VERSION:
            st.setdefault("roots", {})
            st.setdefault("trusted", {})
            if isinstance(st["roots"], dict) and isinstance(st["trusted"], dict):
                return st
    except (OSError, ValueError):
        pass
    return {"version": STATE_VERSION, "roots": {}, "trusted": {}}


def save_state(st: dict) -> None:
    path = state_path()
    G._private_dir(os.path.dirname(path))
    fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(st, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def remember(st: dict, changed: list) -> None:
    """Record the repositories Gnomonkit trusted, so the session-start hook can put back a key Claude Code
    loses. A repository already recorded keeps its first record. Keyed on the path as trusted (already
    resolved by the scan), so nothing on disk is looked at."""
    for rec in changed:
        st["trusted"].setdefault(T.key_norm(rec["repo"]), {
            "repo": rec["repo"], "root": rec.get("root") or "", "by": rec.get("by") or "",
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
