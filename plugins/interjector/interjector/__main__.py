"""Interjector command line.

    python -m interjector sessions                                  recent sessions, pending notes
    python -m interjector say   --session ID|latest "text"          comment at the next tool boundary
    python -m interjector block --session ID|latest --match RE "why"   refuse the next matching tool call
    python -m interjector hold  --session ID|latest "text"          don't let the turn end; continue with this
    python -m interjector watch [--session ID] [--once]             run the watcher's rules
    python -m interjector board [--out FILE] [--watch N] [--json]   every open session on one page

Notes written here are labelled as coming from the user when delivered. Run from the plugin folder
(or with it on PYTHONPATH).

Run from inside a Claude Code session (CLAUDECODE set), say, block and hold first ask the orchestration
grants (~/.gnomonkit/grants.toml) whether a session in this project may message a session in the
target's project; the target's folder comes from `claude agents`, and one it doesn't show is refused.
Refused: exit 2, nothing queued. Run by the user at a plain terminal, nothing is checked. Each checked
decision goes to ~/.gnomonkit/dispatch/audit.jsonl (never the note's text).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

import ij_store as S  # noqa: E402


def _sessions() -> list[tuple[float, str]]:
    root = S.home()
    out = []
    if root.is_dir():
        for d in root.iterdir():
            ev = d / "events.jsonl"
            if ev.is_file():
                out.append((ev.stat().st_mtime, d.name))
    return sorted(out, reverse=True)


def _resolve(session: str) -> str | None:
    known = _sessions()
    if session == "latest":
        return known[0][1] if known else None
    return session if any(sid == session for _, sid in known) else None


def _granted(sid: str) -> bool:
    """The orchestration-grant check for `message`, only inside a Claude Code session. Prints the refusal."""
    import gk_grants as GR
    if not GR.in_claude_code():
        return True
    from interjector import board as B
    entries, _error = B.fetch_agents()
    d = GR.authorize("message", GR.session_folder(entries, sid), entries=entries, session=sid, target=sid)
    if d.allowed:
        return True
    print(GR.refusal_once(d), file=sys.stderr)
    return False


def _write(args, kind: str) -> int:
    sid = _resolve(args.session)
    if not sid:
        print(f"no such session: {args.session} (see `python -m interjector sessions`)", file=sys.stderr)
        return 2
    if not _granted(sid):
        return 2
    S.write_note(sid, kind, args.text, "user", match=getattr(args, "match", None))
    print(f"{kind} queued for {sid}; it is delivered at the session's next step")
    return 0


def _list() -> int:
    for mtime, sid in _sessions()[:20]:
        d = S.home() / sid
        events = sum(1 for _ in open(d / "events.jsonl", encoding="utf-8"))
        pend = len(list((d / "inbox").glob("*.json")))
        print(f"{sid}  last {time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))}  "
              f"{events} events  {pend} pending")
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m interjector")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sessions")
    for name in ("say", "hold"):
        p = sub.add_parser(name)
        p.add_argument("--session", required=True)
        p.add_argument("text")
    b = sub.add_parser("block")
    b.add_argument("--session", required=True)
    b.add_argument("--match", required=True, help="regex matched against '<tool> <command or path>'")
    b.add_argument("text")
    w = sub.add_parser("watch")
    w.add_argument("--session")
    w.add_argument("--once", action="store_true")
    w.add_argument("--interval", type=float, default=5.0)
    g = sub.add_parser("guesses", help="predict-then-confirm guesses paired with your replies (hit rate)")
    g.add_argument("--html", help="write a review page where you mark each guess Right or Wrong")
    bd = sub.add_parser("board", help="every open Claude Code session on one local page, waiting on you first")
    bd.add_argument("--out", help="where to write the page (default: board.html in the Interjector's folder)")
    bd.add_argument("--watch", type=float, metavar="N", help="rewrite the page every N seconds; it reloads itself")
    bd.add_argument("--json", action="store_true", help="print the board as JSON instead of writing the page")
    bd.add_argument("--all", action="store_true", help="also show empty sessions and finished, stopped subs")
    return ap


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.cmd == "sessions":
        return _list()
    if args.cmd == "say":
        return _write(args, "comment")
    if args.cmd == "block":
        return _write(args, "block")
    if args.cmd == "hold":
        return _write(args, "hold")
    if args.cmd == "board":
        from interjector import board as B
        if args.watch is not None and args.watch <= 0:
            print("--watch needs a number of seconds above 0", file=sys.stderr)
            return 2
        try:
            return B.run(out=Path(args.out) if args.out else None, watch=args.watch, as_json=args.json,
                         include_all=args.all)
        except KeyboardInterrupt:
            return 0
    if args.cmd == "guesses":
        from interjector import guesses as G
        pairs = G.collect()
        print(G.summary(pairs))
        if args.html:
            print(f"review page: {G.render_html(pairs, Path(args.html))}")
        return 0
    from interjector.watch import run as watch_run
    return watch_run(session=args.session, once=args.once, interval=args.interval)


if __name__ == "__main__":
    sys.exit(main())
