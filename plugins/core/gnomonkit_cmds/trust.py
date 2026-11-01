"""gnomonkit trust: folder-trust levels per root, and which repositories Claude Code trusts.

    gnomonkit trust [status] [--depth N] [--json]   each root's level and its repositories' trust (changes nothing)
    gnomonkit trust sync [--depth N]                terminal only: trust the repositories that qualify

The levels are [[trust]] tables in ~/.gnomonkit/grants.toml (GNOMONKIT_HOME moves it), set with
`gnomonkit setup security` in a terminal. Per root: `trusted` (Gnomonkit may trust a repository there whose
origin's owner is in `owners`, or with `local_only` one with no remote; with `auto` new clones are trusted
at session start), `per-project` (Gnomonkit trusts nothing; Claude Code asks per folder) or `never`
(Gnomonkit trusts nothing, and session-dispatch starts no session there). The most specific root wins.

Claude Code keys trust on the repository root, in its own file (~/.claude.json, CLAUDE_CONFIG_DIR moves
it): a trusted parent does not cover a repository inside it. `sync` looks one level down each `trusted`
root (deeper with --depth, which also finds repositories nested in others), lists the ones not yet trusted
that qualify and why, and after you type the word shown sets projects["<repository root>"]
.hasTrustDialogAccepted = true on those and nothing else, with a backup in ~/.gnomonkit/trust/backups/
and an audit line in ~/.gnomonkit/trust/audit.jsonl. It never touches `per-project` or `never` roots.

`sync` runs only when stdin and stdout are a terminal: an agent's shell is not, so it gets exit 2 and
nothing changes, and the core guard hook blocks agents from running it. `status` only reads.
"""
from __future__ import annotations

import json
import os
import secrets
import sys

from . import _common as C
from . import grants as GC

_HOOKS = os.path.join(C.core_dir(), "hooks")
if _HOOKS not in sys.path:
    sys.path.insert(0, _HOOKS)

import gk_grants as G  # noqa: E402
import gk_trust as T  # noqa: E402
import trust_write as TW  # noqa: E402

SECURITY_ORDER = 20     # after the grants (10): the levels live in the same file
MAX_DEPTH = 6


def register(sub) -> None:
    p = sub.add_parser("trust", help="folder-trust levels and which repositories Claude Code trusts",
                       description=__doc__.split("\n\n")[0])
    ts = p.add_subparsers(dest="trust_cmd", metavar="<status|sync>")
    q = ts.add_parser("status", help="each root's level and its repositories' trust (changes nothing)")
    _depth(q)
    q.add_argument("--json", action="store_true", help="machine-readable output")
    q.set_defaults(func=cmd_status)
    q = ts.add_parser("sync", help="trust the repositories under trusted roots that qualify (terminal only)")
    _depth(q)
    q.set_defaults(func=cmd_sync)
    p.set_defaults(func=cmd_status, depth=1, json=False)


def _depth(q) -> None:
    q.add_argument("--depth", type=int, default=1, metavar="N",
                   help="how many folder levels below each root to look (default 1; deeper also finds "
                        "repositories inside repositories)")


def _check_depth(n) -> int:
    if not 1 <= int(n) <= MAX_DEPTH:
        raise C.CommandError("--depth must be between 1 and %d" % MAX_DEPTH)
    return int(n)


# ---- reading -------------------------------------------------------------------------------------
def survey(levels, cfg, depth: int = 1) -> tuple:
    """([(root, [(repo, trusted, qualifies or None, reason)])], complete): every repository under every root,
    listed under the root that governs it."""
    by_root = {r.key: [] for r in levels.roots}
    seen = set()
    complete = True
    index = T.key_index(cfg.projects) if cfg.ok else {}
    for r in levels.roots:
        if not os.path.isdir(r.root):
            continue
        repos, done = T.find_repos(r.root, depth)
        complete = complete and done
        for repo in repos:
            k = G.norm(repo)
            if k in seen:
                continue
            seen.add(k)
            gov = T.governing(repo, levels)
            if gov is None:
                continue
            trusted = T.is_trusted(cfg.projects, repo, index, resolved=True) if cfg.ok else None
            if gov.level == "trusted":
                ok, why = T.qualifies(repo, gov)
            else:
                ok, why = None, ""
            by_root[gov.key].append((repo, trusted, ok, why))
    return [(r, by_root[r.key]) for r in levels.roots], complete


def uncovered(levels, cfg) -> list:
    """The folders Claude Code's file trusts that no root governs, so no level applies to them. Keys as the
    file spells them, compared as written (nothing on disk is looked at)."""
    if not cfg.ok:
        return []
    out = {}
    for k, v in cfg.projects.items():
        if isinstance(v, dict) and v.get(T.TRUST_KEY) is True and T.governing(k, levels, resolved=True) is None:
            out.setdefault(T.key_norm(k), k)
    return [out[k] for k in sorted(out)]


LEVEL_NOTE = {"per-project":"Gnomonkit trusts nothing here; Claude Code asks per folder",
              "never": "Gnomonkit trusts nothing here, and session-dispatch starts no session here"}


def _state_word(trusted, ok) -> str:
    if trusted is None:
        return "unknown"
    if trusted:
        return "trusted"
    if ok:
        return "pending"
    return "not trusted"


def cmd_status(args) -> int:
    depth = _check_depth(getattr(args, "depth", 1))
    levels = T.load_levels()
    cfg = T.read_claude_config()
    rows, complete = survey(levels, cfg, depth) if levels.ok else ([], True)
    if getattr(args, "json", False):
        print(json.dumps({
            "levels": {"path": levels.path, "status": levels.status, "problems": levels.problems},
            "claude_config": {"path": cfg.path, "ok": cfg.ok, "problem": cfg.problem},
            "depth": depth, "complete": complete,
            "roots": [{"root": r.root, "level": r.level, "auto": r.auto, "owners": r.owners,
                       "local_only": r.local_only, "exists": os.path.isdir(r.root),
                       "repos": [{"repo": repo, "trusted": t, "qualifies": ok, "reason": why}
                                 for repo, t, ok, why in repos]} for r, repos in rows],
            "uncovered": uncovered(levels, cfg)}, indent=2))
        return 0 if levels.status in ("ok", "missing") else 1
    print("Folder-trust levels: %s" % levels.path)
    if levels.status == "missing":
        print("  none yet, so Gnomonkit trusts nothing for you and Claude Code asks per folder.")
        print("  Set them in a terminal: %s" % C.command_line("setup", "security"))
    elif not levels.ok:
        print("  %s, so Gnomonkit trusts nothing and session-dispatch refuses to start sessions:" % levels.status)
        for p in levels.problems:
            print("    - " + p)
        print("  Fix the file in an editor, or run in a terminal: %s" % C.command_line("setup", "security"))
    elif not levels.roots:
        print("  no [[trust]] roots, so Gnomonkit trusts nothing for you.")
    print("Claude Code's file: %s%s" % (cfg.path, "" if cfg.ok else " (%s)" % cfg.problem))
    pending = 0
    for r, repos in rows:
        print("\n%s\n  %s" % (r.root, r.describe() if r.level == "trusted" else "level %s: %s"
                              % (r.level, LEVEL_NOTE[r.level])))
        if not os.path.isdir(r.root):
            print("  (no such folder)")
            continue
        if not repos:
            print("  no repositories found %s" % ("directly under it" if depth == 1 else "within %d levels" % depth))
        for repo, trusted, ok, why in repos:
            word = _state_word(trusted, ok)
            pending += word == "pending"
            print("  %-12s %s%s" % (word, repo, "  (%s)" % why if why and word != "trusted" else ""))
    loose = uncovered(levels, cfg)
    if loose:
        print("\nTrusted in Claude Code under no root, so no level applies (%d):" % len(loose))
        for k in loose[:10]:
            print("  " + k)
        if len(loose) > 10:
            print("  and %d more (--json lists them all)" % (len(loose) - 10))
    if not complete:
        print("\nThe scan stopped early (too many folders); a smaller --depth or a narrower root sees all of it.")
    if pending:
        print("\n%d pending: run in a terminal to trust them: %s" % (pending, C.command_line("trust", "sync")))
    return 0 if levels.status in ("ok", "missing") else 1


# ---- sync ----------------------------------------------------------------------------------------
def confirm_word() -> str:
    """A word to type, new each time, so a script that pipes in a fixed answer doesn't fit."""
    return "trust-%04d" % secrets.randbelow(10000)


def _need_terminal(what: str) -> None:
    if not C.is_terminal():
        raise C.CommandError("refused: `%s` changes which folders Claude Code trusts, so it runs only in a terminal "
                             "where you type the answer yourself (stdin and stdout must both be a terminal; an "
                             "agent's shell is not). Nothing was changed." % what)


def record_trusted(changed: list) -> None:
    """Remember what Gnomonkit trusted, so the session-start hook puts back a key Claude Code loses."""
    try:
        st = TW.read_state()
        TW.remember(st, changed)
        TW.save_state(st)
    except OSError:
        pass


def cmd_sync(args) -> int:
    _need_terminal("trust sync")
    depth = _check_depth(getattr(args, "depth", 1))
    levels = T.load_levels()
    if levels.status == "missing":
        raise C.CommandError("there are no folder-trust levels yet, so nothing qualifies. Set them first: %s"
                             % C.command_line("setup", "security"))
    if not levels.ok:
        raise C.CommandError("the folder-trust levels in %s are %s (%s); nothing was changed"
                             % (levels.path, levels.status, "; ".join(levels.problems)))
    cfg = T.read_claude_config()
    if not cfg.ok:
        raise C.CommandError("%s is left as it is: %s. Nothing was changed." % (cfg.path, cfg.problem))
    found, complete = T.candidates(levels, cfg.projects, depth=depth)
    pending = [c for c in found if c.pending]
    skipped = [c for c in found if not c.qualifies and not c.trusted]
    print("Claude Code's file: %s" % cfg.path)
    if skipped:
        print("\nLeft alone (they don't qualify):")
        for c in skipped:
            print("  %s  (%s)" % (c.repo, c.reason))
    if not complete:
        print("\nThe scan stopped early (too many folders); some repositories may not be listed.")
    if not pending:
        print("\nNothing to trust: every repository that qualifies is trusted already.")
        return 0
    print("\nWill mark trusted in Claude Code (only hasTrustDialogAccepted, nothing else):")
    for c in pending:
        print("  %s\n      under %s: %s" % (c.repo, c.root.root, c.reason))
    # The paths as the scan found them. The writer checks each one again once you have typed the word: a
    # folder swapped for a link meanwhile, or one that no longer qualifies, is refused.
    items = [TW.Item(c.repo, c.root.root, c.reason) for c in pending]
    C.require_tty(confirm_word())
    try:
        res = TW.set_trusted(items, by="sync", path=cfg.path)
    except TW.WriteError as e:
        raise C.CommandError(str(e)) from None
    record_trusted(res.changed)
    done = []
    for rec in res.changed:
        if rec["repo"] not in done:
            done.append(rec["repo"])
    for repo in done:
        print("Trusted: %s" % repo)
    for repo, why in res.refused + res.failed:
        print("Not trusted: %s (%s)" % (repo, why))
    if res.backups:
        print("Backup of the file as it was: %s" % res.backups[0])
    print("Logged to %s" % G.audit_path("trust"))
    return 1 if res.failed or res.refused else 0


# ---- gnomonkit setup security --------------------------------------------------------------------
INTRO = """\
== Folder trust ==

Claude Code asks once per repository whether you trust it, and a background session (claude --bg) refuses a
repository you were never asked about. Gnomonkit can answer for you where you say so. For each folder that holds
projects, pick a level:

  trusted      Gnomonkit may mark a repository there trusted when its origin's owner is one you list (or,
               if you allow it, when it has no remote). With auto on, new clones are trusted when a session
               starts. An owner says where a clone came from, not who wrote what is in it.
  per-project  Gnomonkit trusts nothing there; Claude Code asks you per repository, as it does now.
  never        Gnomonkit trusts nothing there, and session-dispatch starts no session there.

A folder inside another can have its own level; the most specific one wins.
"""


def _ask_root(another: bool) -> str | None:
    while True:
        raw = GC._ask("%s folder to set a level for (absolute path; empty: done)" % ("Another" if another else "A"))
        if not raw:
            return None
        p = os.path.expanduser(raw.strip())
        if os.path.isabs(p) and os.path.isdir(p):
            return G.resolve(p)
        print("  Not an existing folder (give an absolute path): %s" % raw)


def _projects_root() -> str | None:
    raw = (os.environ.get("GNOMONKIT_PROJECTS_ROOT") or "").strip()
    p = os.path.expanduser(raw) if raw else ""
    return G.resolve(p) if p and os.path.isabs(p) and os.path.isdir(p) else None


def _ask_level() -> str:
    while True:
        a = GC._ask("Level: trusted, per-project or never", "per-project").lower()
        if a in T.LEVELS:
            return a
        print("  Answer trusted, per-project or never.")


def _ask_owners() -> list:
    while True:
        raw = GC._ask("Whose repositories? GitHub accounts or organisations, comma-separated (host/name for an "
                      "account on another host; empty: none)")
        owners = GC._split(raw)
        bad = [o for o in owners if not T._OWNER.match(o)]
        if not bad:
            return owners
        print("  Not an account name: %s" % ", ".join(bad))


def ask_tables() -> list:
    tables, seen = [], set()
    offered = _projects_root()
    if offered and not GC._yes("Set a level for your projects folder, %s?" % offered, True):
        offered = None
    while True:
        root, offered = (offered or _ask_root(bool(tables))), None
        if root is None:
            return tables
        if G.norm(root) in seen:
            print("  That folder has a level already.")
            continue
        seen.add(G.norm(root))
        level = _ask_level()
        t = {"root": root, "level": level}
        if level == "trusted":
            t["auto"] = GC._yes("Trust new repositories there when a session starts (auto)?", True)
            t["owners"] = _ask_owners()
            t["local_only"] = GC._yes("Also trust repositories there with no remote (local_only)?", False)
            if not t["owners"] and not t["local_only"]:
                print("  With no owners and local_only off, no repository there qualifies yet.")
        tables.append(t)


def _levels_lines(levels) -> list:
    return ["  %s: %s" % (r.root, r.describe()) for r in levels.roots]


def save_levels(data: dict, event: dict) -> str:
    """Write the grants file with these [[trust]] tables: checked as the readers check it, the old file kept
    as grants.toml.bak, written atomically (0600), the change logged to the trust audit log."""
    path = G.grants_path()
    out = dict(data)
    if not out.get("trust"):
        out.pop("trust", None)
    text = GC.render(out)
    parsed = G.parse_toml(text)
    status, problems, _ = G.check(parsed)
    tstatus, tproblems, _roots = T.check_trust(parsed)
    if status != "ok" or tstatus != "ok":    # never write a file the readers would refuse
        raise C.CommandError("the new file would not pass the check (%s); nothing was changed"
                             % "; ".join(problems + tproblems))
    try:
        with open(path, "rb") as fh:
            old = fh.read()
    except FileNotFoundError:
        old = None
    if old is not None:
        C.atomic_write(path + ".bak", old)
    C.atomic_write(path, text)
    if not G.audit_append(dict(event, roots=len(out.get("trust") or [])), G.audit_path("trust")):
        print("gnomonkit: the change was made but could not be logged to %s" % G.audit_path("trust"),
              file=sys.stderr)
    return path


def security_setup(_args) -> int:
    if not C.is_terminal():
        raise C.CommandError("refused: the folder-trust questions run only in a terminal. Nothing was changed.")
    print(INTRO)
    levels = T.load_levels()
    if levels.status not in ("ok", "missing"):
        print("Your folder-trust levels have a problem, so Gnomonkit trusts nothing and session-dispatch refuses "
              "to start sessions until it is fixed:")
        for p in levels.problems:
            print("  - " + p)
        print("Fix %s in an editor, or move it aside and run this again. Levels unchanged.\n" % levels.path)
        return 0
    if levels.roots:
        print("Your levels now (%s):" % levels.path)
        print("\n".join(_levels_lines(levels)))
        if not GC._yes("Replace them with new answers?"):
            print("Folder-trust levels unchanged.\n")
            return 0
    else:
        print("You have no folder-trust levels yet: Gnomonkit trusts nothing for you.\n")
    tables = ask_tables()
    if not tables:
        print("Folder-trust levels unchanged.\n")
        return 0
    _loaded, data = GC._writable()
    data["trust"] = tables
    print("\nThis will be %s:\n" % G.grants_path())
    print(GC.render(data))
    C.require_tty(confirm_word())
    path = save_levels(data, {"event": "levels-setup",
                              "levels": [t["level"] for t in tables]})
    print("Written: %s\n" % path)
    if any(t["level"] == "trusted" for t in tables):
        print("To trust the repositories already there, run in a terminal: %s" % C.command_line("trust", "sync"))
        print("To see each one's trust: %s\n" % C.command_line("trust", "status"))
    return 0
