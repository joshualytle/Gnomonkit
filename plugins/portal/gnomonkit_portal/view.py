"""The only data the page ever sees: a whitelisted view of the collector's metrics file.

The collector's file carries more than a dashboard needs: working-directory paths, the short
descriptions agents gave their subagents, correction-watch prompt samples. None of that is served.
Every section here is rebuilt from scratch, keeping

  - numbers (any int or float under a plain snake_case key, so new numeric fields such as a
    subscription share pass through without a code change),
  - a few named strings that the collector itself generates (statuses, labels, targets, reasons),
  - project labels (the last folder name of a project, never its path),
  - session titles, capped, and dropped entirely when hide_titles is set.

Titles come from transcripts, so they are untrusted: the page writes them with textContent only,
and dumps() escapes <, > and & so the JSON can never be read as markup either.
A section the metrics file does not carry is simply absent; the page shows an empty state for it.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
import time

KEY = re.compile(r"[a-z0-9_]{1,48}")
TITLE_MAX = 120
TEXT_MAX = 300
TOP_ROWS = 20
TOP_SESSIONS = 25


def _num(v) -> bool:
    """A real number: never a bool, and never NaN or an infinity (they are not JSON)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and (isinstance(v, int) or math.isfinite(v))


def _list(v) -> list:
    """v when it is a list, else an empty one: a malformed file gives an empty section, not an error."""
    return v if isinstance(v, list) else []


def nums(d) -> dict:
    """Every numeric scalar under a plain key."""
    if not isinstance(d, dict):
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and KEY.fullmatch(k) and _num(v)}


# Control characters, and the characters that reorder or hide text (bidi overrides and isolates, zero-width
# spaces and joiners, the byte-order mark): a title could otherwise display as something it is not.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_INVISIBLE = re.compile(r"[\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")


def text(v, n: int = TEXT_MAX):
    if not isinstance(v, str):
        return None
    v = _INVISIBLE.sub("", _CONTROL.sub(" ", v)).strip()
    return v[:n] if v else None


def strs(d, keys, n: int = TEXT_MAX) -> dict:
    if not isinstance(d, dict):
        return {}
    out = {}
    for k in keys:
        t = text(d.get(k), n)
        if t is not None:
            out[k] = t
    return out


def labels(d) -> dict:
    if not isinstance(d, dict):
        return {}
    return {k: text(v, 60) for k, v in d.items() if isinstance(k, str) and KEY.fullmatch(k) and isinstance(v, str)}


def basename(p) -> str | None:
    if not isinstance(p, str) or not p.strip():
        return None
    return text(re.split(r"[\\/]", p.rstrip("\\/"))[-1], 80)


class Labeller:
    """Project keys (transcript folder names, which encode a full path) to the project's folder name."""

    def __init__(self, m: dict):
        self.map: dict = {}
        comp = m.get("compaction")
        if isinstance(comp, dict):
            for k, p in comp.items():
                if isinstance(p, dict) and basename(p.get("cwd")):
                    self.map[k] = basename(p.get("cwd"))
        for row in _list(m["usage"].get("projects")) if isinstance(m.get("usage"), dict) else []:
            if isinstance(row, dict) and isinstance(row.get("project"), str) and text(row.get("label"), 80):
                lab = row["label"]
                self.map.setdefault(row["project"], basename(lab) or text(lab, 80))
        bud = m.get("budgets")
        if isinstance(bud, dict):
            for row in _list(bud.get("projects")):
                if isinstance(row, dict) and isinstance(row.get("project"), str) and basename(row.get("cwd")):
                    self.map.setdefault(row["project"], basename(row.get("cwd")))

    def __call__(self, key) -> str:
        if not isinstance(key, str) or not key:
            return "unknown"
        if key in self.map:
            return self.map[key]
        # An unmapped transcript folder name ("C--work-demo"): show only its last segment.
        return text(re.split(r"[\\/]|--", key)[-1], 80) or "unknown"


def _short(sid) -> str | None:
    return text(sid, 8) if isinstance(sid, str) else None


def _group_rows(g, name_fn=lambda k: text(k, 80), flags=()) -> list:
    """Each group's numbers and name, plus the named true/false flags (true only when the source says true)."""
    if not isinstance(g, dict):
        return []
    rows = []
    for k, v in g.items():
        if isinstance(v, dict):
            rows.append(dict(nums(v), name=name_fn(k) or "unknown", **{f: v.get(f) is True for f in flags}))
    return rows


def _effectiveness(m: dict, lab: Labeller, titles: dict) -> dict | None:
    out = {}
    h = m.get("headline")
    if isinstance(h, dict):
        hd = {}
        for k in ("tokens_saved", "subagent_spend", "spawns", "worth_it"):
            x = h.get(k)
            if isinstance(x, dict):
                hd[k] = dict(nums(x), **strs(x, ("label",), 60))
                if isinstance(x.get("parts"), dict):
                    hd[k]["parts"] = nums(x["parts"])
        if hd:
            out["headline"] = hd
    s = m.get("subagents")
    if isinstance(s, dict):
        sub = nums(s)
        sub["spend"] = nums(s.get("spend"))
        sub["labels"] = labels(s.get("labels"))
        sub["by_type"] = _group_rows(s.get("by_type"))[:TOP_ROWS]
        sub["by_project"] = _group_rows(s.get("by_project"), lab)[:TOP_ROWS]
        sub["by_week"] = _group_rows(s.get("by_week"), flags=("partial",))
        nw = []
        for r in _list(s.get("not_worth_it_spawns"))[:TOP_ROWS]:
            if not isinstance(r, dict):
                continue
            row = {k: v for k, v in nums(r).items() if k in ("returned", "net_kept_out", "parent_turns_after",
                                                             "reads_avoided", "kept_out", "tool_calls")}
            total = r["spend"].get("total") if isinstance(r.get("spend"), dict) else None
            row["spend"] = total if _num(total) else None
            row.update(strs(r, ("type", "week"), 60))
            row["project"] = lab(r.get("project"))
            nw.append(row)                    # never the spawn's description: it is prompt-derived
        sub["not_worth_it"] = nw
        out["subagents"] = sub
    c = m.get("main_context")
    if isinstance(c, dict):
        mc = nums(c)
        mc["labels"] = labels(c.get("labels"))
        over = c.get("over")
        mc["over"] = {k: nums(v) for k, v in over.items() if isinstance(v, dict) and re.fullmatch(r"\d{1,9}", str(k))} \
            if isinstance(over, dict) else {}
        top = []
        for r in _list(c.get("per_session"))[:10]:
            if isinstance(r, dict):
                row = nums(r)
                row["session"] = _short(r.get("session"))
                row["project"] = lab(r.get("project"))
                t = titles.get(r["session"]) if isinstance(r.get("session"), str) else None
                if t:
                    row["title"] = t
                top.append(row)
        mc["top_sessions"] = top
        out["main_context"] = mc
    comp = m.get("compaction")
    if isinstance(comp, dict):
        rows = []
        for k, p in comp.items():
            if not isinstance(p, dict):
                continue
            row = nums(p)
            row.update({"c_" + kk: vv for kk, vv in nums(p.get("compaction")).items()})
            row["project"] = lab(k)
            rows.append(row)
        out["compaction"] = sorted(rows, key=lambda r: -(r.get("sessions") or 0))[:TOP_ROWS * 2]
    b = m.get("budgets")
    if isinstance(b, dict):
        bd = nums(b)
        bd.update(strs(b, ("label", "generated_at"), 80))
        bd["projects"] = []
        for r in _list(b.get("projects")):
            if isinstance(r, dict):
                row = nums(r)
                row.update(strs(r, ("confidence", "rule", "reason", "source"), 200))
                row["project"] = lab(r.get("project"))
                bd["projects"].append(row)
        out["budgets"] = bd
    hk = m.get("hooks")
    if isinstance(hk, dict):
        hooks = {}
        rt = hk.get("router")
        if isinstance(rt, dict):
            hooks["router"] = _group_rows(rt)[:TOP_ROWS]
        for k in ("interlock", "correction_watch", "freshness", "palace"):
            if isinstance(hk.get(k), dict):
                hooks[k] = nums(hk[k])
        if isinstance(hk.get("interlock"), dict) and isinstance(hk["interlock"].get("outcomes"), dict):
            hooks["interlock"]["outcomes"] = nums(hk["interlock"]["outcomes"])
        out["hooks"] = hooks
    d = m.get("digest")
    if isinstance(d, dict) and d.get("command"):
        out["digest"] = nums(d)               # the wrapper's name is the user's own; numbers only
    return out or None


def _readiness(m: dict) -> dict | None:
    r = m.get("readiness")
    if not isinstance(r, dict) or not isinstance(r.get("rows"), list):
        return None
    rows = []
    for x in r["rows"]:
        if not isinstance(x, dict):
            continue
        row = nums(x)
        row.update(strs(x, ("id", "metric", "display", "target", "status", "label", "definition", "note"), 400))
        if isinstance(x.get("higher_is_better"), bool):
            row["higher_is_better"] = x["higher_is_better"]
        row["detail"] = nums(x.get("detail"))
        rows.append(row)
    return {"rows": rows, "summary": nums(r.get("summary")), **strs(r, ("worst", "label"), 40)}


# The collector's subscription note names the schedule file's real path, and `source` is that path, so
# neither is served: the page gets the status and this text instead.
SUB_NOTES = {
    "missing": "No subscription schedule, so there is no share of plan. Add one to ~/.gnomonkit/subscription.json "
               "(or the file the core plugin's subscription_file option names): a list of "
               '{"from": "YYYY-MM", "plan": "<plan name>", "monthly_usd": <price>}, one entry per plan change.',
    "invalid": "The subscription schedule has no usable entry, so there is no share of plan. Each entry needs "
               '"from" (YYYY-MM) and a numeric "monthly_usd".',
}


def _subscription(u: dict) -> dict | None:
    s = u.get("subscription")
    if not isinstance(s, dict):
        return None
    status = s.get("status") if s.get("status") in ("ok", "missing", "invalid") else "missing"
    sched = []
    for r in _list(s.get("schedule")):
        if isinstance(r, dict) and _num(r.get("monthly_usd")):
            sched.append({"from": text(r.get("from"), 7), "plan": text(r.get("plan"), 60), "monthly_usd": r["monthly_usd"]})
    out = {"status": status, "schedule": sched}
    if text(s.get("method"), 400):
        out["method"] = text(s.get("method"), 400)
    if status in SUB_NOTES:
        out["note"] = SUB_NOTES[status]
    return out


def _month_row(r: dict) -> dict:
    row = dict(nums(r), **strs(r, ("month", "plan", "label", "note"), 200))
    for k in ("covers_full_month", "complete"):
        if isinstance(r.get(k), bool):
            row[k] = r[k]
    return row


def _usage(m: dict, lab: Labeller, hide_titles: bool) -> dict | None:
    u = m.get("usage")
    if not isinstance(u, dict):
        return None
    out = strs(u, ("label", "note", "daily_timezone"), 300)
    out["total"] = nums(u.get("total"))
    sub = _subscription(u)
    if sub:
        out["subscription"] = sub
    out["months"] = [_month_row(r) for r in _list(u.get("months")) if isinstance(r, dict)]
    if isinstance(u.get("month_to_date"), dict):
        out["month_to_date"] = _month_row(u["month_to_date"])
    out["session_count"] = u.get("session_count") if _num(u.get("session_count")) else None
    out["daily"] = [dict(nums(r), **strs(r, ("date",), 12)) for r in _list(u.get("daily")) if isinstance(r, dict)]
    out["projects"] = [dict(nums(r), project=lab(r.get("project"))) for r in _list(u.get("projects"))[:TOP_ROWS * 2]
                       if isinstance(r, dict)]
    sess = []
    for r in _list(u.get("sessions"))[:TOP_SESSIONS]:
        if not isinstance(r, dict):
            continue
        row = dict(nums(r), **strs(r, ("first_ts", "last_ts"), 25))
        row["session"] = _short(r.get("session"))
        row["project"] = lab(r.get("project"))
        if not hide_titles and text(r.get("title"), TITLE_MAX):
            row["title"] = text(r.get("title"), TITLE_MAX)
        sess.append(row)
    out["sessions"] = sess
    mods = u.get("models")
    out["models"] = [dict(nums(v), model=text(k, 60)) for k, v in mods.items() if isinstance(v, dict)] \
        if isinstance(mods, dict) else []
    return out


# Activities ("Where it goes"). Names are rebuilt from parts the collector generates: its own activity
# names, and a subagent type, MCP server or tool name after a known prefix. A shell program and an MCP
# server are served by name. Command text, arguments and paths never are: a process is redrawn from its
# step shapes as program names, with a subcommand kept only for the tools listed here.
ACT_NAME = re.compile(r"[a-z][a-z /-]{1,40}")
ACT_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,59}")
PROGRAM = re.compile(r"[a-z0-9][a-z0-9_.+-]{0,39}")
# How many subcommand words a tool keeps, and the only words that may be kept: a word outside this set
# could be a remote, a host or a branch, so it ends the step.
SUBCOMMANDS = {"git": 1, "gh": 2, "npm": 1, "pnpm": 1, "yarn": 1, "bun": 1, "cargo": 1, "dotnet": 1, "flutter": 1,
               "docker": 1, "kubectl": 1, "go": 1, "uv": 1, "pip": 1, "terraform": 1, "helm": 1}
SUBWORDS = set("""add commit push pull fetch merge rebase checkout switch worktree stash tag clone reset status diff
log show branch remote ls-remote cherry-pick revert rm mv restore apply init pr issue run api release repo create view
list checks watch edit comment close ready review install uninstall test build publish exec ci plan get describe logs
compose up down ps restart sync lock upgrade update analyze pub clean check clippy fmt vet mod tidy deploy start
stop delete rollout format lint""".split())
PY_MODULES = {"pytest", "pip", "venv", "unittest", "mypy", "ruff", "black", "build", "coverage"}
TOP_ACTIVITIES = 40
TOP_PROCESSES = 10


def activity_name(v) -> str:
    if not isinstance(v, str):
        return "unknown"
    for pre in ("subagent: ", "mcp: ", "other: "):
        if v.startswith(pre):
            part = v[len(pre):]
            return pre + part if ACT_PART.fullmatch(part) else pre + "unnamed"
    return v if ACT_NAME.fullmatch(v) else "unknown"


def safe_step(step) -> str:
    """One automate-repeats step shape ("git commit", "python leakcheck.py", "mcp__srv__tool") -> what may
    be shown: the program, plus a known tool's subcommand words; an MCP step as its server."""
    if not isinstance(step, str) or not step.strip():
        return "?"
    if step.startswith("mcp__"):
        srv = step.split("__")[1] if step.count("__") >= 2 else ""
        return "mcp: " + srv if ACT_PART.fullmatch(srv or "") else "mcp"
    words = step.split()
    prog = words[0] if PROGRAM.fullmatch(words[0]) else "?"
    keep = [prog]
    if prog.startswith("python") and len(words) > 2 and words[1] == "-m" and words[2] in PY_MODULES:
        keep += ["-m", words[2]]
    for w in words[1:1 + SUBCOMMANDS.get(prog, 0)]:
        if w not in SUBWORDS:
            break
        keep.append(w)
    return " ".join(keep)


def _act_row(r: dict, nested: bool = True) -> dict:
    row = nums(r)
    row["activity"] = activity_name(r.get("activity"))
    row["group"] = r.get("group") if r.get("group") in ("core", "subagent", "mcp", "other") else "core"
    progs = []
    for p in (r.get("programs") or [])[:TOP_ROWS] if isinstance(r.get("programs"), list) else []:
        if isinstance(p, dict) and isinstance(p.get("program"), str) and PROGRAM.fullmatch(p["program"]):
            progs.append(dict(nums(p), program=p["program"]))
    if progs:
        row["programs"] = progs
    if nested and isinstance(r.get("inner"), list):
        row["inner"] = [_act_row(x, False) for x in r["inner"][:TOP_ROWS] if isinstance(x, dict)]
    return row


def _activities(m: dict) -> dict | None:
    a = m.get("activities")
    if not isinstance(a, dict) or not isinstance(a.get("activities"), list):
        return None
    out = strs(a, ("label",), 80)
    if text(a.get("method"), 1200):
        out["method"] = text(a.get("method"), 1200)
    out["total"] = nums(a.get("total"))
    out["activities"] = [_act_row(r) for r in a["activities"][:TOP_ACTIVITIES] if isinstance(r, dict)]
    procs = []
    for r in (a.get("processes") or [])[:TOP_PROCESSES] if isinstance(a.get("processes"), list) else []:
        if isinstance(r, dict) and isinstance(r.get("shape"), list):
            procs.append(dict(nums(r), steps=[safe_step(s) for s in r["shape"][:8]]))   # never r["pattern"]
    out["processes"] = procs
    out["process_rule"] = nums(a.get("process_rule"))
    out["context_bands"] = [nums(b) for b in a.get("context_bands") or [] if isinstance(b, dict)] \
        if isinstance(a.get("context_bands"), list) else []
    return out


def public_view(m: dict, hide_titles: bool = False) -> dict:
    """The served view. `m` is the collector's metrics dict (or anything; bad input gives empty sections)."""
    if not isinstance(m, dict):
        m = {}
    lab = Labeller(m)
    titles = {}
    if not hide_titles:
        for r in _list(m["usage"].get("sessions")) if isinstance(m.get("usage"), dict) else []:
            if isinstance(r, dict) and isinstance(r.get("session"), str) and text(r.get("title"), TITLE_MAX):
                titles[r["session"]] = text(r.get("title"), TITLE_MAX)
    src = m.get("sources") if isinstance(m.get("sources"), dict) else {}
    meta = dict(strs(m, ("generated_at", "window_from", "version"), 40))
    if _num(m.get("since_days")):
        meta["since_days"] = m["since_days"]
    meta["sources"] = {k: v for k, v in nums(src).items() if k in ("sessions", "transcripts", "scanned", "cache_hits")}
    meta["hide_titles"] = bool(hide_titles)
    sections = {}
    e = _effectiveness(m, lab, titles)
    if e:
        sections["effectiveness"] = e
    r = _readiness(m)
    if r:
        sections["readiness"] = r
    u = _usage(m, lab, hide_titles)
    if u:
        sections["usage"] = u
    act = _activities(m)
    if act:
        sections["activities"] = act
    return {"schema": 1, "meta": meta, "sections": sections}


# Work items (core's `gnomonkit work`). The summary file core writes has no issue bodies and no comment
# text; this view keeps even less: the repository, number, state, ages, a link rebuilt from the
# repository and number (never a URL from the file), and, unless hide_titles is set, the title and who
# holds the claim. Titles are written by anyone who can open an issue, so they are untrusted like
# session titles: capped here, written with textContent on the page.
WORK_STATES = ("active", "blocked", "review", "open")
WORK_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
WORK_MAX = 200


def _epoch(v):
    if _num(v):
        return float(v)
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d.*", v):
        try:
            return _dt.datetime.strptime(v[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_dt.timezone.utc).timestamp()
        except ValueError:
            return None
    return None


def work_view(data, hide_titles: bool = False, now: float | None = None) -> dict:
    """The served Work tab. `data` is core's work summary file (or anything; bad input gives no items)."""
    now = time.time() if now is None else now
    repos_in = data.get("repos") if isinstance(data, dict) and isinstance(data.get("repos"), dict) else {}
    items, repos = [], []
    for repo, entry in repos_in.items():
        if not isinstance(repo, str) or not WORK_REPO.fullmatch(repo) or ".." in repo or not isinstance(entry, dict):
            continue
        hours = entry.get("stale_hours") if _num(entry.get("stale_hours")) else 24
        n_items = 0
        for i in (entry.get("items") or [])[:WORK_MAX] if isinstance(entry.get("items"), list) else []:
            if not isinstance(i, dict) or type(i.get("number")) is not int or i["number"] <= 0 \
                    or i.get("state") not in WORK_STATES:
                continue
            created, last = _epoch(i.get("created")), _epoch(i.get("last_activity")) or _epoch(i.get("updated"))
            row = {"repo": repo, "number": i["number"], "state": i["state"],
                   "url": "https://github.com/%s/issues/%d" % (repo, i["number"]),
                   "age_s": int(max(0, now - created)) if created else None,
                   "idle_s": int(max(0, now - last)) if last else None}
            row["stale"] = row["state"] == "active" and (i.get("stale") is True or (
                hours > 0 and row["idle_s"] is not None and row["idle_s"] > hours * 3600))
            if not hide_titles:
                row["title"] = text(i.get("title"), TITLE_MAX) or "(untitled)"
                for k, n in (("claimed_by", 80), ("machine", 60)):
                    if text(i.get(k), n):
                        row[k] = text(i.get(k), n)
            items.append(row)
            n_items += 1
        repos.append({"repo": repo, "items": n_items, **strs(entry, ("read_at",), 25)})
    items.sort(key=lambda r: (WORK_STATES.index(r["state"]), r["idle_s"] if r["idle_s"] is not None else 1e18))
    counts = {s: sum(1 for r in items if r["state"] == s) for s in WORK_STATES}
    counts["stale"] = sum(1 for r in items if r["stale"])
    return {"schema": 1, "items": items, "repos": sorted(repos, key=lambda r: r["repo"]), "counts": counts,
            "hide_titles": bool(hide_titles)}


# Folder trust (core's `gnomonkit trust status --json`, read by trust.py). It names the user's own folders,
# so the server serves it in loopback mode only, and even there every path under the home folder is shown
# as ~/..., a repository by its path relative to its root, and every string is cleaned and capped.
TRUST_LEVELS = ("trusted", "per-project", "never")
TRUST_ROOTS = 100
TRUST_REPOS = 500
TRUST_UNCOVERED = 500
PATH_MAX = 300


def _fold(s: str) -> str:
    return s.casefold() if os.name == "nt" else s


def _homes(home: str | None = None) -> list:
    """The home folder's spellings (as given and resolved), with / separators, longest first."""
    h = home if home is not None else os.path.expanduser("~")
    out = set()
    for p in (h, os.path.realpath(h)) if h and h != "~" else ():
        p = p.replace("\\", "/").rstrip("/")
        if len(p) > 1:
            out.add(p)
    return sorted(out, key=len, reverse=True)


def tilde(p, homes) -> str | None:
    """A path with / separators and the home folder as ~."""
    if not isinstance(p, str) or not p.strip():
        return None
    s = p.replace("\\", "/")
    for h in homes:
        if _fold(s.rstrip("/")) == _fold(h):
            return "~"
        if _fold(s).startswith(_fold(h) + "/"):
            s = "~/" + s[len(h) + 1:]
            break
    return text(s, PATH_MAX)


def _tilde_text(v, homes, n: int = TEXT_MAX):
    """Free text (a reason, a problem) with every spelling of the home folder replaced by ~."""
    if not isinstance(v, str):
        return None
    for h in homes:
        for form in {h, h.replace("/", "\\")}:
            v = re.sub(re.escape(form), "~", v, flags=re.I if os.name == "nt" else 0)
    return text(v, n)


def _relative(repo, root) -> str | None:
    if not isinstance(repo, str) or not isinstance(root, str):
        return None
    r, b = repo.replace("\\", "/"), root.replace("\\", "/").rstrip("/")
    if _fold(r).startswith(_fold(b) + "/") and len(r) > len(b) + 1:
        return text(r[len(b) + 1:], PATH_MAX)
    return None


def _trust_state(x: dict) -> str:
    t = x.get("trusted")
    if t is not True and t is not False:
        return "unknown"
    if t:
        return "trusted"
    return "pending" if x.get("qualifies") is True else "not trusted"


def trust_view(data, home: str | None = None) -> dict:
    """The served Trust tab. `data` is core's `trust status --json` (or anything: bad input gives an empty
    view with a note). `home` is for tests; the home folder is found otherwise."""
    if not isinstance(data, dict):
        return {"schema": 1, "error": "The trust levels could not be read. Is Gnomonkit core installed?",
                "roots": [], "uncovered": [], "counts": {}}
    homes = _homes(home)
    lv = data.get("levels") if isinstance(data.get("levels"), dict) else {}
    cc = data.get("claude_config") if isinstance(data.get("claude_config"), dict) else {}
    counts = {"trusted": 0, "pending": 0, "not_trusted": 0, "unknown": 0}
    roots = []
    for r in _list(data.get("roots"))[:TRUST_ROOTS]:
        if not isinstance(r, dict) or r.get("level") not in TRUST_LEVELS or not tilde(r.get("root"), homes):
            continue
        row = {"root": tilde(r["root"], homes), "level": r["level"], "exists": r.get("exists") is not False,
               "repos": []}
        if r["level"] == "trusted":
            row.update(auto=r.get("auto") is True, local_only=r.get("local_only") is True,
                       owners=[o for o in (text(x, 100) for x in _list(r.get("owners"))[:20]) if o])
        for x in _list(r.get("repos"))[:TRUST_REPOS]:
            if not isinstance(x, dict):
                continue
            name = _relative(x.get("repo"), r["root"]) or tilde(x.get("repo"), homes)
            if not name:
                continue
            state = _trust_state(x)
            counts[state.replace(" ", "_")] += 1
            rep = {"name": name, "state": state}
            why = _tilde_text(x.get("reason"), homes, 200)
            if why and state != "trusted":
                rep["reason"] = why
            row["repos"].append(rep)
        roots.append(row)
    groups: dict = {}
    for k in _list(data.get("uncovered"))[:TRUST_UNCOVERED]:
        p = tilde(k, homes)
        if p:
            parent, _, name = p.rstrip("/").rpartition("/")
            groups.setdefault(parent or p, set()).add(name if parent else "")
    uncovered = [{"parent": k, "folders": sorted(v - {""})}
                 for k, v in sorted(groups.items(), key=lambda kv: (-len(kv[1]), _fold(kv[0])))]
    counts["uncovered"] = sum(len(g["folders"]) or 1 for g in uncovered)
    return {"schema": 1, "roots": roots, "uncovered": uncovered, "counts": counts,
            "complete": data.get("complete") is not False,
            "levels": {"status": text(lv.get("status"), 30) or "unknown", "path": tilde(lv.get("path"), homes),
                       "problems": [p for p in (_tilde_text(x, homes) for x in _list(lv.get("problems"))[:20]) if p]},
            "claude_config": {"ok": cc.get("ok") is True, "path": tilde(cc.get("path"), homes),
                              **({"problem": _tilde_text(cc.get("problem"), homes)} if cc.get("problem") else {})}}


def dumps(obj) -> bytes:
    """JSON with <, > and & escaped, so no part of it can ever be parsed as markup."""
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)   # NaN is not JSON
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").encode("utf-8")
