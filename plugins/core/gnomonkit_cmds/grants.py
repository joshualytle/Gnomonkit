"""gnomonkit grants: which sessions may start, hand off to, message or stop sessions in which projects.

    gnomonkit grants list [--json]                     the grants, numbered, and any problem with the file
    gnomonkit grants check --from DIR --to DIR --action A [--json]   would this be allowed? (changes nothing)
    gnomonkit grants add --from PATTERN --to PATTERN [--to ...] [--except PATTERN ...]
                         --actions spawn,handoff,message,stop [--agents claude]      terminal only
    gnomonkit grants remove N                          terminal only
    gnomonkit grants deny-rules                        the permissions.deny lines for your Claude Code settings

The file is ~/.gnomonkit/grants.toml (GNOMONKIT_HOME moves it). Default deny: with no file, a file with a
problem, or no matching grant, session-dispatch and the interjector refuse when an agent asks them to act.
A PATTERN is an absolute folder, `<dir>/*` (any folder directly under it), `<dir>/**` (any depth), and in
`--to` also `same-project` (the requesting session's own project) or `*` (anywhere).

`add` and `remove` change the file only when stdin and stdout are a terminal and you type the word shown on
screen: an agent's shell is not a terminal, so it gets exit 2 and nothing changes. The core guard hook also
blocks agents from running them or editing the file. They keep the previous file as grants.toml.bak and
log the change to the dispatch audit log. `list`, `check` and `deny-rules` only read, so anyone may run them.

`gnomonkit setup security` runs this module's questions (security_setup) in a terminal.
"""
from __future__ import annotations

import json
import os
import secrets
import sys

from . import _common as C

_HOOKS = os.path.join(C.core_dir(), "hooks")
if _HOOKS not in sys.path:
    sys.path.insert(0, _HOOKS)

import gk_grants as G  # noqa: E402

SECURITY_ORDER = 10     # grants first; folder trust follows
HEADER = ("Gnomonkit orchestration grants: which sessions may start, hand off to, message or stop which others.\n"
          "Default deny: no file, a problem in it, or no matching grant means refused.\n"
          "Only you change this file, with `gnomonkit grants` or `gnomonkit setup security` in a terminal.\n"
          "Comments are not kept; the previous version is saved beside this file as grants.toml.bak.")
LAYOUT = [("", ["version"]), ("grant", []), ("trust", [])]
WORDS = ("allow", "grant", "permit", "confirm", "agree")


def register(sub) -> None:
    p = sub.add_parser("grants", help="orchestration grants: which sessions may start or steer which others",
                       description=__doc__.split("\n\n")[0])
    gs = p.add_subparsers(dest="grants_cmd", metavar="<list|check|add|remove|deny-rules>")
    q = gs.add_parser("list", help="the grants, numbered, and any problem with the file")
    q.add_argument("--json", action="store_true", help="machine-readable output")
    q.set_defaults(func=cmd_list)
    q = gs.add_parser("check", help="would a session in one folder be allowed to act in another? (changes nothing)")
    q.add_argument("--from", dest="from_", required=True, metavar="DIR", help="the requesting session's folder")
    q.add_argument("--to", required=True, metavar="DIR", help="the folder it would act in")
    q.add_argument("--action", required=True, choices=G.ACTIONS)
    q.add_argument("--agent", default="claude")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_check)
    q = gs.add_parser("add", help="add a grant (terminal only)")
    q.add_argument("--from", dest="from_", required=True, metavar="PATTERN",
                   help="where the requesting session runs: a folder, <dir>/* or <dir>/**")
    q.add_argument("--to", action="append", required=True, metavar="PATTERN",
                   help="where it may act (repeat for more): a folder, <dir>/*, <dir>/**, same-project or *")
    q.add_argument("--except", dest="except_", action="append", default=[], metavar="PATTERN",
                   help="never these, whatever --to says (repeat for more)")
    q.add_argument("--actions", required=True, help="comma-separated: %s, or all" % ", ".join(G.ACTIONS))
    q.add_argument("--agents", default="claude", help="comma-separated agent names (default: claude)")
    q.set_defaults(func=cmd_add)
    q = gs.add_parser("remove", help="remove grant N, as numbered by `list` (terminal only)")
    q.add_argument("number", type=int)
    q.set_defaults(func=cmd_remove)
    q = gs.add_parser("deny-rules", help="the permissions.deny lines to add to your Claude Code user settings")
    q.set_defaults(func=cmd_deny_rules)
    p.set_defaults(func=cmd_list)


# ---- reading -------------------------------------------------------------------------------------
def _status_line(loaded) -> str:
    if loaded.status == "missing":
        return "not created yet, so everything is refused (`%s` sets it up)" % C.command_line("setup", "security")
    if not loaded.ok:
        return "%s, so everything is refused until it is fixed:" % loaded.status
    return "%d grant%s" % (len(loaded.grants), "" if len(loaded.grants) == 1 else "s")


def _grant_lines(g) -> list:
    out = ["%2d. from %s" % (g.index, g.from_.raw),
           "    to   %s" % ", ".join(p.raw for p in g.to)]
    if g.except_:
        out.append("    except %s" % ", ".join(p.raw for p in g.except_))
    ignored = [a for a in g.agents if a not in G.AGENTS_SUPPORTED]
    out.append("    may  %s  (agents: %s%s)" % (", ".join(g.actions), ", ".join(g.agents),
                                                "; %s not supported yet, ignored" % ", ".join(ignored) if ignored
                                                else ""))
    return out


def cmd_list(args) -> int:
    loaded = G.load()
    trust = (loaded.data or {}).get("trust") if isinstance(loaded.data, dict) else None
    if getattr(args, "json", False):
        print(json.dumps({"path": loaded.path, "status": loaded.status, "problems": loaded.problems,
                          "grants": [dict(g.source, number=g.index) for g in loaded.grants],
                          "trust_tables": len(trust) if isinstance(trust, list) else 0}, indent=1))
        return 0
    print("Grants file: %s" % loaded.path)
    print("Status:      %s" % _status_line(loaded))
    for problem in (loaded.problems if loaded.status not in ("ok", "missing") else []):
        print("  - " + problem)
    for g in loaded.grants:
        print("\n".join(_grant_lines(g)))
    if isinstance(trust, list) and trust:
        print("Folder-trust levels: %d [[trust]] table%s (`gnomonkit trust status` shows them)"
              % (len(trust), "" if len(trust) == 1 else "s"))
    print("Run outside a Claude Code session (a person at a terminal), dispatch and the interjector don't check "
          "grants: you need no permission from yourself.")
    return 0


def cmd_check(args) -> int:
    d = G.decide(G.repo_root(args.from_), G.repo_root(args.to), args.action, args.agent)
    if args.json:
        print(json.dumps(d.as_dict(), indent=1))
    elif d.allowed:
        print("allowed by grant %d: a session in %s may %s in %s" % (d.grant, d.from_dir, args.action, d.to_dir))
    else:
        print(G.refusal_text(d, _prog()))
    return 0 if d.allowed else 2


def _prog() -> str:
    return C.command_line()


# ---- writing -------------------------------------------------------------------------------------
def _store_pattern(raw: str, field: str) -> str:
    """A pattern as the file keeps it: resolved, forward slashes. ValueError says what is wrong."""
    s = str(raw).strip()
    if s in (G.ANYWHERE, G.SAME_PROJECT):
        G.parse_pattern(s, field)
        return s
    t = s.replace("\\", "/")
    suffix = ""
    for end in ("/**", "/*"):
        if t.endswith(end):
            suffix, t = end, t[:-len(end)] or "/"
            break
    if not os.path.isabs(os.path.expanduser(t)) and not (len(t) == 2 and t[1] == ":"):
        raise ValueError("%s: %r is not an absolute path" % (field, s))
    stored = G.resolve(t if not (len(t) == 2 and t[1] == ":") else t + "/")
    stored = stored.rstrip("/") + suffix if suffix else stored
    G.parse_pattern(stored, field)
    return stored


def _split(raw: str) -> list:
    return [x.strip() for x in str(raw or "").split(",") if x.strip()]


def build_grant(from_, to, except_=(), actions=(), agents=("claude",)) -> dict:
    """One [[grant]] table, every entry checked and stored resolved. ValueError says what is wrong."""
    actions = list(G.ACTIONS) if [a.lower() for a in actions] == ["all"] else [a.lower() for a in actions]
    bad = [a for a in actions if a not in G.ACTIONS]
    if not actions or bad:
        raise ValueError("actions must be some of: %s (or all)%s" % (", ".join(G.ACTIONS),
                                                                    "; not %s" % ", ".join(bad) if bad else ""))
    g = {"from": _store_pattern(from_, "from"), "to": [_store_pattern(t, "to") for t in to]}
    if except_:
        g["except"] = [_store_pattern(x, "except") for x in except_]
    g["actions"] = [a for a in G.ACTIONS if a in actions]
    g["agents"] = [a.lower() for a in agents] or ["claude"]
    status, problems, _ = G.check({"version": G.VERSION, "grant": [g]})
    if status != "ok":
        raise ValueError("; ".join(p.replace("grant 1: ", "") for p in problems))
    return g


def _writable() -> tuple:
    """(loaded, data) for a rewrite. A file that exists but can't be read or checked is never overwritten:
    that would lose whatever is in it."""
    loaded = G.load()
    if loaded.status == "missing":
        return loaded, {"version": G.VERSION}
    if not loaded.ok:
        raise C.CommandError("%s is %s, so it is left as it is: %s. Fix it in an editor, or move it aside and run "
                             "`%s`." % (loaded.path, loaded.status, "; ".join(loaded.problems),
                                        C.command_line("setup", "security")))
    return loaded, dict(loaded.data)


def render(data: dict) -> str:
    out = {k: v for k, v in data.items() if not (k == "grant" and not v)}
    out["version"] = G.VERSION
    return C.dumps(out, LAYOUT, HEADER)


def save(data: dict, event: dict) -> str:
    """Back up the old file, write the new one atomically (0600), log the change. Returns the path."""
    path = G.grants_path()
    text = render(data)
    status, problems, _ = G.check(G.parse_toml(text))
    if status != "ok":     # never write a file the readers would refuse
        raise C.CommandError("the new grants would not pass the check (%s); nothing was changed" % "; ".join(problems))
    try:
        with open(path, "rb") as fh:
            old = fh.read()
    except FileNotFoundError:
        old = None
    if old is not None:
        C.atomic_write(path + ".bak", old)
    C.atomic_write(path, text)
    if not G.audit_append(dict(event, grants=len(data.get("grant") or []))):
        print("gnomonkit: the change was made but could not be logged to %s" % G.audit_path(), file=sys.stderr)
    return path


def confirm_word(kind: str = "grant") -> str:
    """A word to type, new each time, so a script that pipes in a fixed answer doesn't fit."""
    return "%s-%04d" % (kind if kind in WORDS else "confirm", secrets.randbelow(10000))


def _need_terminal(what: str) -> None:
    if not C.is_terminal():
        raise C.CommandError("refused: `grants %s` changes a permission, so it runs only in a terminal where you type "
                             "the answer yourself (stdin and stdout must both be a terminal; an agent's shell is "
                             "not). Nothing was changed." % what)


def _toml_block(g: dict) -> str:
    return C.dumps({"grant": [g]}, [("grant", [])]).strip()


def cmd_add(args) -> int:
    _need_terminal("add")
    try:
        g = build_grant(args.from_, args.to, args.except_, _split(args.actions), _split(args.agents))
    except ValueError as e:
        raise C.CommandError(str(e)) from None
    loaded, data = _writable()
    print("This grant will be added to %s:\n" % loaded.path)
    print(_toml_block(g) + "\n")
    print("It lets a session whose project matches `from` %s in the projects `to` names."
          % ", ".join(g["actions"]))
    C.require_tty(confirm_word("grant"))
    data["grant"] = list(data.get("grant") or []) + [g]
    path = save(data, {"event": "grants-add", "grant": g})
    print("Added as grant %d in %s." % (len(data["grant"]), path))
    return 0


def cmd_remove(args) -> int:
    _need_terminal("remove")
    loaded, data = _writable()
    grants = list(data.get("grant") or [])
    if not 1 <= args.number <= len(grants):
        raise C.CommandError("there is no grant %d (%d in the file; `grants list` numbers them)"
                             % (args.number, len(grants)))
    print("This grant will be removed from %s:\n" % loaded.path)
    print(_toml_block(grants[args.number - 1]) + "\n")
    C.require_tty(confirm_word("confirm"))
    gone = grants.pop(args.number - 1)
    data["grant"] = grants
    save(data, {"event": "grants-remove", "grant": gone})
    print("Removed. %d grant%s left." % (len(grants), "" if len(grants) == 1 else "s"))
    return 0


# ---- Claude Code's own deny rules ------------------------------------------------------------------
def _rule_path(path: str) -> str:
    """A path as a Claude Code permission rule writes it: ~/ under the home folder, else //absolute."""
    p = G.resolve(path)
    home = G.resolve(os.path.expanduser("~"))
    if G.norm(p).startswith(G.norm(home).rstrip("/") + "/"):
        return "~/" + p[len(home.rstrip("/")) + 1:]
    if len(p) > 1 and p[1] == ":":
        p = "/" + p[0].lower() + p[2:]
    return "/" + p if p.startswith("/") else "//" + p


SESSION_COMMANDS = ("claude --bg", "claude stop", "claude kill", "claude rm", "claude attach", "claude respawn")


def deny_rules() -> list:
    """Edits to the grants file, and the session commands an agent would otherwise type into a shell
    itself: session-dispatch runs them after checking the grants, so a tool call never needs to."""
    rp = _rule_path(G.grants_path())
    return (["Edit(%s)" % rp, "Write(%s)" % rp]
            + ["%s(%s *)" % (tool, c) for tool in ("Bash", "PowerShell") for c in SESSION_COMMANDS])


def cmd_deny_rules(_args) -> int:
    print_deny_rules()
    return 0


def print_deny_rules() -> None:
    rules = deny_rules()
    print("Claude Code can refuse these itself: an edit to the grants file, and a session command typed straight "
          "into a shell (session-dispatch runs those after checking the grants). Add these lines to permissions.deny "
          "in your user settings (~/.claude/settings.json), or with /permissions in a terminal session. Gnomonkit "
          "never edits settings files for you:\n")
    print('  "permissions": {\n    "deny": [\n%s\n    ]\n  }' % ",\n".join('      "%s"' % r for r in rules))
    print("\nNone of this is a sandbox: an agent with an unrestricted shell can still reach the file by means no "
          "rule foresees. The rules and the guard hook stop the plain cases; the terminal rule makes a deliberate "
          "change need you; the audit log (%s) shows what happened." % G.audit_path())


# ---- gnomonkit setup security ----------------------------------------------------------------------
INTRO = """\
== Orchestration grants ==

Session-dispatch and the interjector let a Claude session start, hand off to, message or stop other
sessions. A grant says which projects a session may do that in. Without one, everything is refused.

  spawn    start a session in a project         handoff  start a fresh session seeded with a handoff
  message  comment on, block or hold a session  stop     stop a session

Examples, with placeholder paths:

  from <projects folder>/*  to same-project            a session may replace or steer sessions in its own
                                                       project only
  from <projects folder>/*  to <projects folder>/*     a session in any project may act in any other
  ... except <projects folder>/<vault>                 but never in that one

The checks run only when an agent asks; run by you in a plain terminal, nothing is checked. An agent may
not change grants: the guard hook blocks it and these commands need your terminal.
"""


def _ask(prompt: str, default: str = "") -> str:
    shown = " [%s]" % default if default else ""
    sys.stdout.write("%s%s: " % (prompt, shown))
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise C.CommandError("no answer (end of input); nothing was changed")
    return line.strip() or default


def _yes(prompt: str, default: bool = False) -> bool:
    while True:
        a = _ask(prompt + (" (y/N)" if not default else " (Y/n)")).lower()
        if not a:
            return default
        if a in ("y", "yes"):
            return True
        if a in ("n", "no"):
            return False
        print("  Answer y or n.")


def _roots() -> list:
    default = (os.environ.get("GNOMONKIT_PROJECTS_ROOT") or "").strip()
    while True:
        raw = _ask("Which folders hold your projects? Absolute paths, separated by ';' (empty: none)", default)
        if not raw:
            return []
        roots, bad = [], []
        for r in (x.strip() for x in raw.split(";")):
            if not r:
                continue
            p = os.path.expanduser(r)
            if os.path.isabs(p) and os.path.isdir(p):
                roots.append(G.resolve(p))
            else:
                bad.append(r)
        if not bad and roots:
            return roots
        print("  Not an existing folder (give absolute paths): %s" % ", ".join(bad or [raw]))


def _actions() -> list:
    names = ", ".join("%d %s" % (i, a) for i, a in enumerate(G.ACTIONS, 1))
    while True:
        raw = _ask("Which actions? Numbers or names, separated by ',' (%s)" % names, "all")
        picked = []
        for x in _split(raw):
            x = x.lower()
            if x == "all":
                picked = list(G.ACTIONS)
                break
            if x.isdigit() and 1 <= int(x) <= len(G.ACTIONS):
                x = G.ACTIONS[int(x) - 1]
            if x not in G.ACTIONS:
                picked = []
                break
            picked.append(x)
        if picked:
            return [a for a in G.ACTIONS if a in picked]
        print("  Choose from: %s, or all." % names)


def _off_limits(roots: list) -> list:
    while True:
        raw = _ask("Is any project off limits (no session may act in it)? Folder names under those folders, or "
                   "paths, separated by ',' (empty: none)")
        out, missing = [], []
        for x in _split(raw):
            p = os.path.expanduser(x)
            if os.path.isabs(p):
                out.append(G.resolve(p))
                continue
            hits = [G.resolve(os.path.join(r, x)) for r in roots if os.path.isdir(os.path.join(r, x))]
            out += hits
            if not hits:
                missing.append(x)
        if not missing:
            return sorted(set(out), key=out.index)
        print("  Not found under %s: %s. Give a folder name that exists there, or a path."
              % (" or ".join(roots), ", ".join(missing)))


def plan_grants(roots: list, cross: bool, actions: list, off_limits: list) -> list:
    """The grants setup writes: one per projects folder."""
    to = [G.pattern_text(r, "children") for r in roots] if cross else [G.SAME_PROJECT]
    return [build_grant(G.pattern_text(r, "children"), to, off_limits, actions) for r in roots]


def security_setup(_args) -> int:
    if not C.is_terminal():
        raise C.CommandError("refused: the grants questions run only in a terminal. Nothing was changed.")
    print(INTRO)
    loaded = G.load()
    if loaded.status not in ("ok", "missing"):
        print("Your grants file has a problem, so everything is refused until it is fixed:")
        for p in loaded.problems:
            print("  - " + p)
        print("Fix %s in an editor, or move it aside and run this again. Grants unchanged." % loaded.path)
        print_deny_rules()
        return 0
    if loaded.grants:
        print("Your grants now (%s):" % loaded.path)
        for g in loaded.grants:
            print("\n".join(_grant_lines(g)))
        if not _yes("Replace them with new answers?"):
            print("Grants unchanged.\n")
            print_deny_rules()
            return 0
    else:
        print("You have no grants yet, so every request from a session is refused.\n")
    roots = _roots()
    if not roots:
        print("No grants written; every request from a session stays refused.\n")
        print_deny_rules()
        return 0
    cross = _yes("May a session in one project start or steer sessions in your OTHER projects? "
                 "(no: only in its own project)", False)
    actions = _actions()
    off = _off_limits(roots)
    try:
        grants = plan_grants(roots, cross, actions, off)
    except ValueError as e:
        raise C.CommandError("those answers don't make a valid grant (%s); nothing was changed" % e) from None
    data = dict(loaded.data) if loaded.ok else {"version": G.VERSION}
    data["grant"] = grants
    print("\nThis will be %s:\n" % loaded.path)
    print(render(data))
    C.require_tty(confirm_word("allow"))
    path = save(data, {"event": "grants-setup", "cross_project": cross, "actions": actions})
    print("Written: %s\n" % path)
    with open(path, encoding="utf-8") as fh:
        print(fh.read())
    print_deny_rules()
    return 0
