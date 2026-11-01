"""gnomonkit usage: each project's share of the weekly usage budget, and what it has used.

    gnomonkit usage [show] [--json]            budget, week, each project's share, use and state
    gnomonkit usage budget AMOUNT usd|tokens    set the weekly budget                              terminal only
    gnomonkit usage allow FOLDER PERCENT        set a project's share; PERCENT `none` removes it   terminal only
    gnomonkit usage lift FOLDER                 let FOLDER go past its share for the rest of this week   terminal only

The file is ~/.gnomonkit/usage-allocation.toml (GNOMONKIT_HOME moves it). The core hook usage_allocation.py
warns at 80% of a project's share and stops new prompts there at 100%; projects with no share are not
limited. Use is counted from the transcripts per UTC ISO week, in API-equivalent dollars or tokens.

`budget`, `allow` and `lift` change the file only when stdin and stdout are a terminal and you type the word
shown on screen: an agent's shell is not a terminal, so it gets exit 2 and nothing changes. The core guard
hook also blocks agents from running them or editing the file. They keep the previous file as
usage-allocation.toml.bak and log the change to ~/.gnomonkit/usage/audit.jsonl. `show` only reads.
"""
from __future__ import annotations

import json
import os
import sys

from . import _common as C

_HOOKS = os.path.join(C.core_dir(), "hooks")
if _HOOKS not in sys.path:
    sys.path.insert(0, _HOOKS)

import gk_grants as G  # noqa: E402
import gk_usage as U  # noqa: E402

HEADER = ("Gnomonkit usage allocation: each project's share of a weekly usage budget.\n"
          "Only you change this file, with `gnomonkit usage` in a terminal.\n"
          "Comments are not kept; the previous version is saved beside this file as usage-allocation.toml.bak.")
LAYOUT = [("", ["version"]), ("budget", ["weekly", "unit"]), ("project", ["folder", "share"]),
          ("lift", ["folder", "week"])]


def register(sub) -> None:
    p = sub.add_parser("usage", help="per-project shares of the weekly usage budget, and what each has used",
                       description=__doc__.split("\n\n")[0])
    us = p.add_subparsers(dest="usage_cmd", metavar="<show|budget|allow|lift>")
    q = us.add_parser("show", help="budget, week, each project's share, use and state (reads only)")
    q.add_argument("--json", action="store_true", help="machine-readable output")
    q.set_defaults(func=cmd_show)
    q = us.add_parser("budget", help="set the weekly budget (terminal only)")
    q.add_argument("amount", type=float)
    q.add_argument("unit", choices=U.UNITS)
    q.set_defaults(func=cmd_budget)
    q = us.add_parser("allow", help="set a project's share in percent; `none` removes it (terminal only)")
    q.add_argument("folder")
    q.add_argument("percent")
    q.set_defaults(func=cmd_allow)
    q = us.add_parser("lift", help="let a project go past its share for the rest of this week (terminal only)")
    q.add_argument("folder")
    q.set_defaults(func=cmd_lift)
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=cmd_show)


# ---- reading -------------------------------------------------------------------------------------
def report(force: bool = True) -> dict:
    alloc = U.load()
    out = {"path": alloc.path, "status": alloc.status, "problems": alloc.problems, "week": U.week_id(),
           "budget": alloc.budget, "unit": alloc.unit, "projects": []}
    if not alloc.ok or not alloc.projects:
        return out
    state = U.refresh(force=force, limit=None)
    out["week"] = state["week"]
    out["complete"] = bool(state.get("complete"))
    used = U.totals(state, alloc)
    out["projects"] = [U.status(alloc, p, used.get(p["folder"], 0.0), state["week"]) for p in alloc.projects]
    return out


def cmd_show(args) -> int:
    r = report()
    if getattr(args, "json", False):
        print(json.dumps(r, indent=2))
        return 0
    if r["status"] == "missing":
        print("No usage allocation: no project is limited. Set one in a terminal with\n  %s\n  %s"
              % (C.command_line("usage", "budget", "<amount>", "usd"),
                 C.command_line("usage", "allow", "<folder>", "<percent>")))
        return 0
    if r["status"] != "ok":
        print("%s is %s, so no project is limited: %s" % (r["path"], r["status"], "; ".join(r["problems"])))
        return 1
    print("Week %s (UTC; it ends %s). Budget: %s a week." % (r["week"], U.WEEK_ENDS, U.fmt(r["budget"], r["unit"])))
    if r["budget"] <= 0:
        print("The budget is 0, so no project is limited.")
    if not r["projects"]:
        print("No project has a share, so none is limited.")
    for p in r["projects"]:
        print("  %-24s %5g%%  %s of %s  (%d%%)  %s\n      %s" % (
            p["label"], p["share"], U.fmt(p["used"], r["unit"]), U.fmt(p["cap"], r["unit"]),
            int(p["fraction"] * 100), p["state"], p["folder"]))
    return 0


# ---- writing -------------------------------------------------------------------------------------
def _need_terminal(what: str) -> None:
    if not C.is_terminal():
        raise C.CommandError("refused: `usage %s` changes a usage limit, so it runs only in a terminal where you "
                             "type the answer yourself (stdin and stdout must both be a terminal; an agent's shell "
                             "is not). Nothing was changed." % what)


def _writable() -> dict:
    alloc = U.load()
    if alloc.status == "missing":
        return {"version": U.VERSION}
    if not alloc.ok:
        raise C.CommandError("%s is %s, so it is left as it is: %s. Fix it in an editor, or move it aside."
                             % (alloc.path, alloc.status, "; ".join(alloc.problems)))
    return dict(alloc.data)


def save(data: dict, event: dict) -> str:
    """Drop lifts for past weeks, check, back up the old file, write the new one (0600), log the change."""
    week = U.week_id()
    data["lift"] = [x for x in data.get("lift") or [] if x.get("week") == week]
    out = {k: v for k, v in data.items() if not (k in ("project", "lift") and not v)}
    out["version"] = U.VERSION
    problems = U.check(out)
    if problems:
        raise C.CommandError("the change would leave the file with a problem (%s); nothing was changed"
                             % "; ".join(problems))
    path = U.alloc_path()
    text = C.dumps(out, LAYOUT, HEADER)
    try:
        with open(path, "rb") as fh:
            old = fh.read()
    except FileNotFoundError:
        old = None
    if old is not None:
        C.atomic_write(path + ".bak", old)
    C.atomic_write(path, text)
    if not G.audit_append(event, G.audit_path("usage")):
        print("gnomonkit: the change was made but could not be logged to %s" % G.audit_path("usage"), file=sys.stderr)
    return path


def _confirm(kind: str) -> None:
    from . import grants as GC
    C.require_tty(GC.confirm_word(kind))


def _folder(raw: str) -> str:
    return os.path.abspath(os.path.expanduser(raw))


def cmd_budget(args) -> int:
    _need_terminal("budget")
    if args.amount < 0:
        raise C.CommandError("the budget can't be below 0 (0 turns the limits off)")
    data = _writable()
    amount = int(args.amount) if args.amount == int(args.amount) else args.amount
    print("The weekly budget becomes %s." % U.fmt(amount, args.unit))
    _confirm("confirm")
    data["budget"] = {"weekly": amount, "unit": args.unit}
    path = save(data, {"event": "usage-budget", "weekly": amount, "unit": args.unit})
    print("Saved in %s." % path)
    return 0


def cmd_allow(args) -> int:
    _need_terminal("allow")
    folder = _folder(args.folder)
    data = _writable()
    projects = [p for p in data.get("project") or [] if U.norm(p["folder"]) != U.norm(folder)]
    if args.percent.strip().lower() == "none":
        print("%s will have no share: it is not limited." % folder)
        share = None
    else:
        try:
            share = float(args.percent.rstrip("%"))
        except ValueError:
            raise C.CommandError("PERCENT must be a number above 0, at most 100, or none") from None
        if not 0 < share <= 100:
            raise C.CommandError("PERCENT must be above 0 and at most 100 (or none to remove the share)")
        share = int(share) if share == int(share) else share
        projects.append({"folder": folder, "share": share})
        print("%s gets %g%% of the weekly budget." % (folder, share))
    _confirm("allow")
    data["project"] = projects
    save(data, {"event": "usage-allow", "folder": folder, "share": share})
    print("Saved.")
    return 0


def cmd_lift(args) -> int:
    _need_terminal("lift")
    folder = _folder(args.folder)
    data = _writable()
    if not any(U.norm(p["folder"]) == U.norm(folder) for p in data.get("project") or []):
        raise C.CommandError("%s has no share, so it isn't limited; nothing to lift" % folder)
    week = U.week_id()
    print("%s may go past its share until the week ends (%s, %s)." % (folder, week, U.WEEK_ENDS))
    _confirm("permit")
    lifts = [x for x in data.get("lift") or [] if not (U.norm(x.get("folder") or "") == U.norm(folder))]
    data["lift"] = lifts + [{"folder": folder, "week": week}]
    save(data, {"event": "usage-lift", "folder": folder, "week": week})
    print("Lifted for %s." % week)
    return 0
