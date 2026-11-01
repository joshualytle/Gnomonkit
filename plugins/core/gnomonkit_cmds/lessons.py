"""gnomonkit lessons: the register of what past sessions taught, and the candidates for it.

    gnomonkit lessons add TEXT [--scope everywhere|repo:NAME|topic:NAME] [--source SESSION]
                               [--entry] [--by user|agent] [--pin] [--same-as L<n> | --new]
    gnomonkit lessons list [--all] [--scope S] [--json]
    gnomonkit lessons pin ID [--off]       always shown at session start (--off: not any more)
    gnomonkit lessons retire ID [--undo]   never shown again (kept in the file)
    gnomonkit lessons reword ID TEXT
    gnomonkit lessons import FILE [--dry-run] [--keep-pins]
    gnomonkit lessons candidates [--project here|all|NAME] [--since DATE] [--limit N] [--json]

`add` looks for a near-duplicate first. When it finds one it prints it, writes nothing and exits 2:
run it again with --same-as L<n> (this session is added to that lesson, so its count goes up) or
--new (a separate lesson). `candidates` lists user messages in the history index that read as
corrections, verbatim and scrubbed, grouped by similarity. Nothing here calls a model: a candidate
becomes a lesson only when the user agrees and the agent (or the user) runs `add`. When lessons are
off here (memory.lessons or GNOMONKIT_LESSONS off, or auto with MemPalace set up), `candidates`
says so and lists nothing.
"""
from __future__ import annotations

import json
import os
import sys

from . import _common as C
from . import _lessons as L


def register(sub) -> None:
    p = sub.add_parser("lessons", help="the lessons register: add, list, pin, retire, import, candidates",
                       description=__doc__.split("\n\n")[0])
    ps = p.add_subparsers(dest="lessons_cmd", metavar="<add|list|pin|retire|reword|import|candidates>")

    q = ps.add_parser("add", help="add a lesson (or an entry), checking for a near-duplicate first")
    q.add_argument("text", nargs="+")
    q.add_argument("--scope", default="everywhere", help="everywhere (default), repo:NAME or topic:NAME")
    q.add_argument("--source", help="the session it came from (counts towards n)")
    q.add_argument("--entry", action="store_true", help="a hand-written standing entry, shown as E<n>; outranks lessons")
    q.add_argument("--by", choices=("user", "agent"), default="user", help="who wrote it (default user)")
    q.add_argument("--pin", action="store_true", help="show it at every session start")
    g = q.add_mutually_exclusive_group()
    g.add_argument("--same-as", metavar="ID", help="it is this existing lesson: add the session to it")
    g.add_argument("--new", action="store_true", help="it is new, even though a similar one exists")
    q.set_defaults(func=cmd_add)

    q = ps.add_parser("list", help="the register (retired items only with --all)")
    q.add_argument("--all", action="store_true")
    q.add_argument("--scope")
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_list)

    for name, fn, flag, hlp in (("pin", cmd_pin, "--off", "show it at every session start"),
                                ("retire", cmd_retire, "--undo", "never show it again (kept in the file)")):
        q = ps.add_parser(name, help=hlp)
        q.add_argument("id", metavar="ID")
        q.add_argument(flag, action="store_true", dest="reverse")
        q.set_defaults(func=fn)

    q = ps.add_parser("reword", help="replace an item's text")
    q.add_argument("id", metavar="ID")
    q.add_argument("text", nargs="+")
    q.set_defaults(func=cmd_reword)

    q = ps.add_parser("import", help="merge a register another tool produced (JSON or JSON lines)")
    q.add_argument("file")
    q.add_argument("--dry-run", action="store_true", help="say what would change, write nothing")
    q.add_argument("--keep-pins", action="store_true",
                   help="keep the file's pins (by default imported items come in unpinned, for you to review)")
    q.set_defaults(func=cmd_import)

    q = ps.add_parser("candidates", help="corrections in your history that may be worth a lesson")
    q.add_argument("--project", default="all", help="here, all (default) or a project name")
    q.add_argument("--since", help="YYYY-MM-DD, or 30d / 2w back")
    q.add_argument("--limit", type=int, default=10)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_candidates)
    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])


def _load() -> tuple:
    path, where = L.register_path()
    try:
        return L.load(path), path, where
    except L.RegisterError as e:
        raise C.CommandError(str(e)) from None


def _line(i: dict) -> str:
    flags = [f for f, on in (("pinned", i["pinned"]), ("retired", i["retired"])) if on]
    return ("%s %s %s" % (L.tag(i, ""), i["text"], "(" + ", ".join(flags) + ")" if flags else "")).rstrip()


# ---- add, reword, pin, retire --------------------------------------------------------------------
def cmd_add(args) -> int:
    items, path, _ = _load()
    try:
        it, how = L.add(items, " ".join(args.text), scope=args.scope, source=args.source,
                        kind="entry" if args.entry else "lesson", by=args.by, same_as=args.same_as, new=args.new,
                        pinned=args.pin)
    except L.NearDuplicate as d:
        print("A similar item is already in the register:", file=sys.stderr)
        for s, i in d.matches[:3]:
            print("  %s (similarity %.2f) %s" % (L.label(i), s, i["text"]), file=sys.stderr)
        first = L.label(d.matches[0][1])
        raise C.CommandError("nothing was written. Run it again with --same-as %s (it is that lesson: this session "
                             "is added and its count goes up) or --new (a separate lesson)." % first) from None
    except ValueError as e:
        raise C.CommandError(str(e)) from None
    L.save(items, path)
    if how == "merged":
        print("%s now comes from %d sessions: %s" % (L.label(it), it["n"], it["text"]))
    else:
        print("added %s (%s): %s" % (L.label(it), it["scope"], it["text"]))
    return 0


def _change(args, fn) -> int:
    items, path, _ = _load()
    try:
        it = L.find(items, args.id)
    except ValueError as e:
        raise C.CommandError(str(e)) from None
    msg = fn(it)
    it["updated"] = L._now()
    L.save(items, path)
    print(msg)
    return 0


def cmd_pin(args) -> int:
    def f(it):
        it["pinned"] = not args.reverse
        return "%s %s" % (L.label(it), "unpinned" if args.reverse else "pinned: shown at every session start")
    return _change(args, f)


def cmd_retire(args) -> int:
    def f(it):
        it["retired"] = not args.reverse
        return "%s %s" % (L.label(it), "back in use" if args.reverse else "retired: never shown again")
    return _change(args, f)


def cmd_reword(args) -> int:
    try:
        text = L.check_text(" ".join(args.text))
    except ValueError as e:
        raise C.CommandError(str(e)) from None

    def f(it):
        it["text"] = text
        return "%s reworded: %s" % (L.label(it), text)
    return _change(args, f)


# ---- list, import --------------------------------------------------------------------------------
def cmd_list(args) -> int:
    items, path, where = _load()
    shown = [i for i in items if (args.all or not i["retired"]) and (not args.scope or i["scope"] == args.scope)]
    if args.json:
        print(json.dumps({"path": path, "where": where, "items": shown}, indent=1, ensure_ascii=False))
        return 0
    print("Lessons register: %s (%s), %d items%s" % (path, where, len(shown), "" if args.all else ", retired hidden"))
    for i in sorted(shown, key=lambda i: (not i["pinned"], i["kind"] != "entry", -i["n"], i["id"])):
        print("  " + _line(i))
    if not items:
        print("  (empty) Add one with: %s" % C.command_line("lessons", "add", '"<the lesson>"', "--scope", "everywhere"))
    return 0


def cmd_import(args) -> int:
    items, path, _ = _load()
    if not os.path.isfile(args.file):
        raise C.CommandError("no such file: %s" % args.file)
    try:
        counts = L.import_items(items, args.file, keep_pins=args.keep_pins)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        raise C.CommandError("can't import %s (%s); nothing was written." % (args.file, e)) from None
    if not args.dry_run:
        L.save(items, path)
    print("%s %d new, %d merged into existing items, %d skipped (retired, rejected or unreadable)%s."
          % ("would import:" if args.dry_run else "imported:", counts["added"], counts["merged"], counts["skipped"],
             "" if args.dry_run else " -> " + path))
    if counts["unpinned"]:
        print("  %d came pinned in the file and %s unpinned: a pinned item is put in front of every session. "
              "Read them (%s), then pin the ones you want (%s), or import again with --keep-pins."
              % (counts["unpinned"], "would come in" if args.dry_run else "came in",
                 C.command_line("lessons", "list"), C.command_line("lessons", "pin", "ID")))
    return 0


# ---- candidates ----------------------------------------------------------------------------------
def _is_correction():
    hooks = os.path.join(C.core_dir(), "hooks")
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    import correction_watch
    return correction_watch.is_correction


def cmd_candidates(args) -> int:
    from . import _history as H
    on, why = L.lessons_mode()
    if not on:
        msg = ("lessons are off here (%s); nothing to offer. To turn them on: %s."
               % (why, C.command_line("profile", "set", "memory.lessons=on")))
        print(json.dumps({"groups": [], "off": why}) if args.json else msg)
        return 0
    try:
        q = H.Query("", project=args.project, since=args.since, limit=args.limit)
    except ValueError as e:
        raise C.CommandError(str(e)) from None
    import sqlite3
    try:
        st = H.Store(readonly=True)
    except FileNotFoundError:
        raise C.CommandError("no history index yet; it builds at session start, or run: %s"
                             % C.command_line("history", "index")) from None
    except H.IndexError_ as e:
        raise C.CommandError(str(e)) from None
    except sqlite3.Error as e:
        raise C.CommandError("the history index can't be read just now (%s); try again in a moment." % e) from None
    try:
        where, params = q.filters()
        sql = ("SELECT c.text, c.session, c.project, c.ts FROM chunks c WHERE c.role = 'user' AND c.origin = "
               "'transcript' AND c.seq = 0%s ORDER BY c.ts DESC LIMIT 20000" % "".join(" AND " + w for w in where))
        rows = [{"text": r[0], "session": r[1], "project": r[2], "ts": r[3]} for r in st.con.execute(sql, params)]
    except sqlite3.Error as e:
        raise C.CommandError("the history index can't be read just now (%s); try again in a moment." % e) from None
    finally:
        st.close()
    items, _, _ = _load()
    groups = L.candidates(rows, items, _is_correction(), limit=max(1, min(args.limit, 50)))
    if args.json:
        print(json.dumps({"groups": groups}, indent=1, ensure_ascii=False))
        return 0
    if not groups:
        print("No corrections found in the indexed history%s." % ("" if args.project == "all" else " for that project"))
        return 0
    print("Corrections from your history (the user's own words, scrubbed), most sessions first. Offer the top "
          "ones to the user; write a lesson only for one they agree to, in a sentence of your own:\n  %s\n"
          % C.command_line("lessons", "add", '"<the lesson>"', "--scope", "everywhere|repo:NAME", "--source",
                           "<session>", "--by", "agent"))
    for n, g in enumerate(groups, 1):
        like = (" (resembles %s: use --same-as %s)" % (g["like"], g["like"])) if g["like"] else ""
        print("%d. %d session%s, %d message%s, last %s, %s%s" % (
            n, g["sessions"], "" if g["sessions"] == 1 else "s", g["count"], "" if g["count"] == 1 else "s",
            (g["last"] or "?")[:10], ", ".join(g["projects"][:3]) or "?", like))
        for ex in g["examples"]:
            print('   "%s"' % " ".join(ex.split()))
        if g["session_ids"]:
            print("   sessions: %s" % ", ".join(s[:8] for s in g["session_ids"]))
    return 0
