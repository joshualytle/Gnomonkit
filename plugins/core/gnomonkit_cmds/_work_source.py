"""GitHub work items as a source: the `gh` calls, the scrubber, and records(repo) for the history index.

Work items are GitHub issues labelled `gk:work` plus one state label (`gk:open`, `gk:active`,
`gk:blocked`, `gk:review`); done means closed. `gnomonkit work` (work.py) is the command on top of
this module. This module is also the history index's GitHub source, so it stands alone: standard
library only, nothing at import time but definitions.

    gh(args), gh_json(args)         one `gh` call each (CREATE_NO_WINDOW, no prompts); GhError on failure
    scrub(text)                     secrets out of issue text, before it is shown, sent or indexed
    list_issues(repo, ...)          work items with their comments, through `gh issue list`
    records(repo, since=None)       {"id", "text", "timestamp", "origin", ...} per issue and per comment
    parse_marker / claim_of / last_activity / is_stale     claims and staleness from the comments
    save_summaries, read_summaries  the portal's Work tab file: titles, states, ages, links; never bodies
    note_session, session_items     the items one session claimed or wrote to (compaction carry-over)

Every call to GitHub is a `gh` subprocess that runs with the user's own credentials; nothing here
holds or caches a token. Issue text is untrusted: whoever can comment on the repository wrote it, so
it is scrubbed here and every caller that shows it says it is data, never instructions.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import time

from . import _common as C

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
GH_TIMEOUT_S = 60

WORK_LABEL = "gk:work"
STATES = ("open", "active", "blocked", "review")
STATE_LABELS = {s: "gk:" + s for s in STATES}
LABELS = {  # name -> (colour, description), created on first use in a repository
    WORK_LABEL: ("5319e7", "A Gnomonkit work item (gnomonkit work)"),
    "gk:open": ("0e8a16", "Work item: not started or not claimed"),
    "gk:active": ("1d76db", "Work item: claimed by a session"),
    "gk:blocked": ("b60205", "Work item: waiting on something"),
    "gk:review": ("fbca04", "Work item: waiting for review"),
}
ISSUE_FIELDS = "number,title,body,state,labels,url,createdAt,updatedAt,closedAt,comments"
LIST_FIELDS = "number,title,state,labels,url,createdAt,updatedAt,closedAt,comments"
DEFAULT_STALE_HOURS = 24
LIST_LIMIT = 200


# ---- gh ------------------------------------------------------------------------------------------
class GhError(Exception):
    """A `gh` call that did not succeed. kind: missing | auth | offline | not-found | failed."""

    def __init__(self, message: str, kind: str = "failed"):
        super().__init__(message)
        self.kind = kind


_AUTH = re.compile(r"auth login|not logged in|authentication|HTTP 401|bad credentials|requires authentication", re.I)
_OFFLINE = re.compile(r"could not resolve host|dial tcp|connection (refused|reset|timed out)|network is unreachable|"
                      r"no such host|TLS handshake|i/o timeout|error connecting", re.I)
_NOT_FOUND = re.compile(r"could not resolve to|not found|HTTP 404|no git remotes|none of the git remotes|"
                        r"not a git repository|no default remote", re.I)


def _classify(text: str) -> str:
    for kind, rx in (("auth", _AUTH), ("offline", _OFFLINE), ("not-found", _NOT_FOUND)):
        if rx.search(text or ""):
            return kind
    return "failed"


def gh(args, cwd: str | None = None, timeout: int = GH_TIMEOUT_S) -> str:
    """Run `gh ARGS` and return its stdout. GhError (with a kind) on anything but success."""
    exe = shutil.which("gh")
    if not exe:
        raise GhError("the GitHub CLI (gh) is not installed or not on PATH; install it and run `gh auth login`",
                      "missing")
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", NO_COLOR="1")
    try:
        r = subprocess.run([exe, *args], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env, creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise GhError("the GitHub CLI (gh) is not installed or not on PATH", "missing") from None
    except subprocess.TimeoutExpired:
        raise GhError("gh did not answer within %d s (offline?)" % timeout, "offline") from None
    except OSError as e:
        raise GhError("gh could not start (%s)" % type(e).__name__, "missing") from None
    if r.returncode != 0:
        detail = re.sub(r"\s+", " ", (r.stderr or r.stdout or "").strip())[:300]
        kind = _classify(detail)
        hint = {"auth": " (not logged in? run `gh auth login`)", "offline": " (offline?)"}.get(kind, "")
        raise GhError("gh %s failed: %s%s" % (" ".join(args[:2]), detail or "exit %d" % r.returncode, hint), kind)
    return r.stdout


def gh_json(args, cwd: str | None = None):
    out = gh(args, cwd=cwd)
    try:
        return json.loads(out or "null")
    except ValueError:
        raise GhError("gh %s returned something that is not JSON" % " ".join(args[:2])) from None


# ---- scrubbing -----------------------------------------------------------------------------------
class ScrubError(Exception):
    """The scrubber could not run. Callers drop the text rather than show or store it raw."""


_BASE = []   # [redact function] once loaded: core's existing redactor (learned-triggers)
_EXTRA = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----.*?(?:-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----|\Z)",
                re.S), "[REDACTED:private-key]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED:jwt]"),
    (re.compile(r"(\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@]{1,128}:)[^\s@/]{1,256}@"), r"\1[REDACTED:url-credential]@"),
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Text that could pass for something the harness or a hook injected, rather than a quote from an issue.
_SPOOF = re.compile(r"(?i)(</?\s*(?:system-reminder|command-[a-z-]+|task-notification)\b)|(INTERJECTOR\s*\()")


def _base_redact():
    if not _BASE:
        import importlib.util
        path = os.path.join(C.core_dir(), "skills", "learned-triggers", "file_correction.py")
        spec = importlib.util.spec_from_file_location("_gk_correction_redactor", path)
        if spec is None or spec.loader is None:
            raise ScrubError("core's redactor is missing (%s)" % path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _BASE.append(mod.redact)
    return _BASE[0]


def scrub(text) -> str:
    """Issue text with secrets replaced: core's redactor (tokens, keys, credential assignments,
    authorization headers), then private-key blocks, JWTs and credentials in URLs. Control characters
    go, and text that imitates an injected tag is defanged. ScrubError when the scrubber can't run."""
    if text is None:
        return ""
    try:
        out = _base_redact()(str(text))
        for rx, repl in _EXTRA:
            out = rx.sub(repl, out)
    except ScrubError:
        raise
    except Exception as e:  # noqa: BLE001 - any failure means the text is not shown or stored
        raise ScrubError("the scrubber failed (%s)" % type(e).__name__) from None
    out = _CONTROL.sub(" ", out)
    return _SPOOF.sub(lambda m: m.group(1).replace("<", "&lt;") if m.group(1) else "INTERJECTOR-quoted (", out)


def scrub_or_withhold(text, what: str = "text") -> str:
    try:
        return scrub(text)
    except ScrubError:
        return "[%s withheld: the scrubber could not run]" % what


# ---- issues ----------------------------------------------------------------------------------------
def label_names(issue: dict) -> set:
    out = set()
    for lab in issue.get("labels") or []:
        name = lab.get("name") if isinstance(lab, dict) else lab
        if isinstance(name, str):
            out.add(name)
    return out


def state_of(issue: dict) -> str:
    """open | active | blocked | review, or closed. An open item with no state label is open."""
    if str(issue.get("state") or "").upper() == "CLOSED":
        return "closed"
    names = label_names(issue)
    for s in ("active", "blocked", "review", "open"):
        if STATE_LABELS[s] in names:
            return s
    return "open"


def is_work_item(issue: dict) -> bool:
    return WORK_LABEL in label_names(issue)


def list_issues(repo: str, state: str = "open", since: str | None = None, limit: int = LIST_LIMIT,
                bodies: bool = False, cwd: str | None = None, oldest_first: bool = False) -> list:
    """Work items in `repo`, with their comments. state: open | closed | all. since: an ISO time; only
    items updated at or after it (GitHub search syntax, so a new comment counts as an update).
    oldest_first: least recently updated first, so a list cut at `limit` is the oldest part of the
    range and a reader that keeps a cursor carries on from its last item."""
    args = ["issue", "list", "--repo", repo, "--label", WORK_LABEL, "--state", state, "--limit", str(limit),
            "--json", ISSUE_FIELDS if bodies else LIST_FIELDS]
    terms = ["updated:%s..*" % since] if since else []   # a range rather than >=: no shell metacharacter
    if oldest_first:
        terms.append("sort:updated-asc")
    if terms:
        args += ["--search", " ".join(terms)]
    data = gh_json(args, cwd=cwd)
    return [i for i in data if isinstance(i, dict) and isinstance(i.get("number"), int)] if isinstance(data, list) \
        else []


def view_issue(repo: str, number: int, cwd: str | None = None) -> dict:
    data = gh_json(["issue", "view", str(number), "--repo", repo, "--json", ISSUE_FIELDS], cwd=cwd)
    if not isinstance(data, dict) or not isinstance(data.get("number"), int):
        raise GhError("gh issue view returned no issue for %s#%d" % (repo, number), "not-found")
    return data


# ---- claims and staleness ----------------------------------------------------------------------------
MARKER = re.compile(r"<!--\s*gk:work\s+(?P<attrs>[^>]*?)\s*-->")
_ATTR = re.compile(r'([a-z]+)="([^"]*)"')


def marker(kind: str, session: str, sid: str, machine: str) -> str:
    """The hidden line every comment the work commands write ends with, so claims can be read back."""
    clean = lambda s: re.sub(r'["<>\r\n]', "", str(s or ""))[:120]  # noqa: E731
    return '<!-- gk:work kind="%s" session="%s" sid="%s" machine="%s" -->' % (
        clean(kind), clean(session), clean(sid), clean(machine))


def parse_marker(body) -> dict | None:
    m = MARKER.search(str(body or ""))
    return dict(_ATTR.findall(m.group("attrs"))) if m else None


def parse_time(ts) -> float | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return _dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_dt.timezone.utc).timestamp()
    except ValueError:
        return None


def claim_of(issue: dict) -> dict | None:
    """The latest claim written by `work claim`: {session, sid, machine, at}. None when there is none."""
    best = None
    for c in issue.get("comments") or []:
        if not isinstance(c, dict):
            continue
        m = parse_marker(c.get("body"))
        if m and m.get("kind") == "claim":
            at = parse_time(c.get("createdAt"))
            if best is None or (at or 0) >= (best.get("at") or 0):
                best = {"session": m.get("session") or "", "sid": m.get("sid") or "",
                        "machine": m.get("machine") or "", "at": at}
    return best


def last_activity(issue: dict) -> float | None:
    """When anyone last commented, else when the item was last updated."""
    times = [parse_time(c.get("createdAt")) for c in issue.get("comments") or [] if isinstance(c, dict)]
    times = [t for t in times if t]
    return max(times) if times else parse_time(issue.get("updatedAt"))


def is_stale(issue: dict, stale_hours: float, now: float | None = None) -> bool:
    if state_of(issue) != "active" or stale_hours <= 0:
        return False
    last = last_activity(issue)
    return last is not None and (now or time.time()) - last > stale_hours * 3600


def age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    s = max(0, int(seconds))
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return "%d%s" % (s // n, unit)
    return "now"


# ---- the history index source ------------------------------------------------------------------------
def records(repo: str, since: str | None = None):
    """Yield the history index's records for every work item in `repo` (open and closed) updated at or
    after `since` (an ISO time, or None for all): one for the item (title, state and body) and one
    per comment. Each record: id (stable, so a re-read replaces rather than duplicates), text
    (scrubbed), timestamp (ISO, UTC), origin (the issue or comment URL), plus source "work", repo,
    number, title and state. A record the scrubber fails on is dropped, never yielded raw. The
    caller keeps the cursor: the largest `updated` of the item records it saw. Items come least
    recently updated first, at most LIST_LIMIT of them, so when that many came the caller reads
    again from its cursor. Also refreshes the portal's summary file for the items read (titles and
    states only)."""
    issues = list_issues(repo, state="all", since=since, limit=LIST_LIMIT, bodies=True, oldest_first=True)
    try:
        save_summaries(repo, issues, complete=False)
    except Exception:  # noqa: BLE001 - the summary file is a convenience; the records still flow
        pass
    since_t = parse_time(since) if since else None
    for issue in issues:
        n = issue["number"]
        ref = "%s#%d" % (repo, n)
        state = state_of(issue)
        try:
            title = scrub(issue.get("title") or "")
            text = scrub("%s %s [%s]\n\n%s" % (ref, issue.get("title") or "", state, issue.get("body") or ""))
        except ScrubError:
            continue
        yield {"id": "work:%s" % ref, "text": text, "timestamp": issue.get("updatedAt"),
               "origin": issue.get("url") or "", "source": "work", "repo": repo, "number": n, "title": title,
               "state": state, "updated": issue.get("updatedAt")}
        for c in issue.get("comments") or []:
            if not isinstance(c, dict):
                continue
            created = parse_time(c.get("createdAt"))
            if since_t and created is not None and created < since_t:
                continue
            try:
                body = scrub("%s %s, comment: %s" % (ref, issue.get("title") or "",
                                                     MARKER.sub("", c.get("body") or "")))
            except ScrubError:
                continue
            cid = c.get("id") or c.get("url") or c.get("createdAt")
            yield {"id": "work:%s/comment/%s" % (ref, cid), "text": body.strip(), "timestamp": c.get("createdAt"),
                   "origin": c.get("url") or issue.get("url") or "", "source": "work", "repo": repo, "number": n,
                   "title": title, "state": state}


def repos_to_index() -> list:
    """The repositories the GitHub source reads: the profile's work.repo, then every repository the
    work commands have read on this machine (the summary file's keys)."""
    out = []
    wr = C.checked_profile_value("work.repo") if "work.repo" in C.SCHEMA_BY_KEY else None
    for r in [wr] + sorted((read_summaries().get("repos") or {}).keys()):
        if isinstance(r, str) and r and r.lower() not in {x.lower() for x in out}:
            out.append(r)
    return out


# ---- local files: the portal's summaries and each session's items ----------------------------------
def work_dir() -> str:
    return os.path.join(C.gnomonkit_home(), "work")


def summaries_path() -> str:
    return os.path.join(work_dir(), "items.json")


def _read_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def read_summaries() -> dict:
    return _read_json(summaries_path())


def summary(repo: str, issue: dict, stale_hours: float = DEFAULT_STALE_HOURS) -> dict:
    """What the portal may show of one item: no body, no comment text."""
    claim = claim_of(issue) if state_of(issue) == "active" else None
    return {"number": issue.get("number"), "title": scrub_or_withhold(issue.get("title") or "", "title")[:200],
            "state": state_of(issue), "url": "https://github.com/%s/issues/%s" % (repo, issue.get("number")),
            "created": issue.get("createdAt"), "updated": issue.get("updatedAt"),
            "last_activity": last_activity(issue), "claimed_by": (claim or {}).get("session") or None,
            "machine": (claim or {}).get("machine") or None, "stale": is_stale(issue, stale_hours)}


def save_summaries(repo: str, issues: list, complete: bool, stale_hours: float = DEFAULT_STALE_HOURS) -> None:
    """Record what is open in `repo`. complete=True means `issues` is every open item there (so items
    missing from it are gone); otherwise only the given items are added, updated or dropped (closed)."""
    data = read_summaries()
    repos = data.setdefault("repos", {})
    entry = repos.setdefault(repo, {})
    items = {} if complete else {str(i.get("number")): i for i in entry.get("items") or [] if isinstance(i, dict)}
    for issue in issues:
        if not isinstance(issue, dict) or not isinstance(issue.get("number"), int) or not is_work_item(issue):
            if isinstance(issue, dict) and isinstance(issue.get("number"), int):
                items.pop(str(issue["number"]), None)
            continue
        if state_of(issue) == "closed":
            items.pop(str(issue["number"]), None)
        else:
            items[str(issue["number"])] = summary(repo, issue, stale_hours)
    entry["items"] = sorted(items.values(), key=lambda i: i.get("number") or 0)
    entry["read_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry["stale_hours"] = stale_hours
    data["version"] = 1
    C.atomic_write(summaries_path(), json.dumps(data, indent=1, sort_keys=True) + "\n")


def session_path(sid: str) -> str:
    """One file per session. The name rule matches compact_carryover.record_path, which reads it."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", sid)[:120] or "default"
    return os.path.join(work_dir(), "sessions", safe + ".json")


def note_session(sid: str, repo: str, number: int, title: str, claimed: bool | None = None,
                 closed: bool = False) -> None:
    """Remember that session `sid` wrote to this item; `claimed` True/False changes the claim, None keeps it."""
    if not sid:
        return
    path = session_path(sid)
    data = _read_json(path)
    items = data.setdefault("items", {})
    key = "%s#%d" % (repo, number)
    cur = items.get(key) if isinstance(items.get(key), dict) else {}
    cur.update({"repo": repo, "number": number, "title": str(title or "")[:200], "touched": time.time()})
    if claimed is not None:
        cur["claimed"] = bool(claimed)
    cur.setdefault("claimed", False)
    if closed:
        cur["closed"], cur["claimed"] = True, False
    items[key] = cur
    data["session_id"] = sid
    C.atomic_write(path, json.dumps(data, indent=1, sort_keys=True) + "\n")


def session_items(sid: str) -> list:
    """[{repo, number, title, claimed, closed, touched}] for the items session `sid` wrote to."""
    if not sid:
        return []
    items = _read_json(session_path(sid)).get("items") or {}
    return [v for v in items.values() if isinstance(v, dict) and isinstance(v.get("number"), int)
            and isinstance(v.get("repo"), str)]
