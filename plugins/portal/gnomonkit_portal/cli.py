"""run_portal.py serve | refresh | snapshot | token | login-url | status"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import VERSION, paths, refresh, server, snapshot, token, updates

DEFAULT_PORT = server.DEFAULT_PORT


def _redirect_std(d: Path):
    """pythonw has no stdout or stderr; give stray output somewhere to land."""
    if sys.stdout is None or sys.stderr is None:
        paths.private_dir(d)
        fh = paths.open_private_append(d / "portal-stdio.txt")
        fh.reconfigure(line_buffering=True)
        sys.stdout = sys.stdout or fh
        sys.stderr = sys.stderr or fh


def _token_path(a) -> Path:
    return Path(a.token_file) if getattr(a, "token_file", None) else paths.data_dir() / "token"


def _token_for(a) -> str | None:
    env = os.environ.get("GNOMONKIT_PORTAL_TOKEN")
    if env:
        if not token.valid(env):
            raise SystemExit("GNOMONKIT_PORTAL_TOKEN must be at least %d URL-safe characters" % token.MIN_LEN)
        return env
    p = _token_path(a)
    if os.path.lexists(p):                 # a dangling symlink is refused, never taken for "no token"
        t = token.read(p)
        if not t:
            raise SystemExit("the token file is not a valid token; run: run_portal.py token init --force")
        return t
    if getattr(a, "token_file", None):
        raise SystemExit("no token file at the path given; run: run_portal.py token init --token-file <path>")
    return None


def cmd_serve(a) -> int:
    d = paths.data_dir()
    _redirect_std(d)
    server.setup_logging(d, to_stderr=sys.stderr is not None and not a.quiet)
    cfg_file = paths.load_config(d)
    cfg = server.Config(
        host=a.host, port=a.port, token=_token_for(a), behind_proxy=a.behind_proxy,
        allowed_hosts=set(a.allowed_host or []), data_dir=d,
        hide_titles=a.hide_titles or cfg_file["hide_titles"],
        memory=a.memory or cfg_file["memory"],
        refresh_min_interval=int(cfg_file["refresh_min_interval"]),
        secure_cookie=a.secure_cookie,
        collector=Path(a.collector) if a.collector else None)
    try:
        srv = server.make_server(cfg)
    except server.StartRefused as e:
        server.log.error("%s", e)
        print(str(e), file=sys.stderr)
        return 2
    except OSError as e:                  # port in use, or reserved (Windows excluded port ranges)
        msg = "could not bind %s:%d (%s); pick another --port" % (a.host, a.port, e.strerror or type(e).__name__)
        server.log.error("%s", msg)
        print(msg, file=sys.stderr)
        return 2
    st = srv.portal_state
    host = a.host if ":" not in a.host.strip("[]") else "[%s]" % a.host.strip("[]")
    server.log.info("portal %s on http://%s:%d auth=%s memory=%s", VERSION, host, a.port, st.mode,
                    "on" if st.memory else "off")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_refresh(a) -> int:
    d = paths.data_dir()
    _redirect_std(d)
    server.setup_logging(d, to_stderr=False)
    rec = refresh.run(a.trigger, since_days=a.since, collector=Path(a.collector) if a.collector else None, d=d)
    server.log.info("refresh (%s): %s", a.trigger, "ok" if rec.get("ok") else rec.get("error"))
    if a.trigger == "nightly":
        # The sync gate's scheduled check rides on the nightly task (docs/SYNC.md); it never fails the refresh.
        sync = updates.check_due()
        server.log.info("sync check: %s", {True: "ok", False: "failed", None: "core not found"}[sync])
    if sys.stdout:
        print("refresh %s%s" % ("ok" if rec.get("ok") else "failed: ", "" if rec.get("ok") else rec.get("error")))
    if not rec.get("ok"):
        return 1
    if a.snapshot:
        # Replaces only a file this command wrote before, so a mistyped path cannot overwrite anything else.
        try:
            out = snapshot.write(Path(a.snapshot), d,
                                 hide_titles=a.hide_titles or paths.load_config(d)["hide_titles"],
                                 replace_snapshot=True, with_trust=a.trust)
        except snapshot.SnapshotRefused as e:
            server.log.warning("snapshot not written: %s", e)
            if sys.stderr:
                print("snapshot not written: %s" % e, file=sys.stderr)
            return 1
        server.log.info("snapshot written")
        if sys.stdout:
            print("snapshot written: %s" % out)
    return 0


def cmd_snapshot(a) -> int:
    d = paths.data_dir()
    try:
        out = snapshot.write(Path(a.out), d, hide_titles=a.hide_titles or paths.load_config(d)["hide_titles"],
                             force=a.force, with_trust=a.trust)
    except snapshot.SnapshotRefused as e:
        print(str(e), file=sys.stderr)
        return 2
    print("snapshot written: %s (readable by you only; it holds the same aggregates and titles as the "
          "live page%s)" % (out, ", and your folder-trust levels" if a.trust else ""))
    return 0


def cmd_token(a) -> int:
    p = _token_path(a)
    if a.action == "init":
        if a.force and os.path.lexists(p):
            p.unlink()
        _, created = token.ensure(p)
        print(("created " if created else "kept the existing ") + "token file: %s" % p)
        return 0
    if a.action == "check":
        print("token: present" if token.read(p) else "token: missing")
        return 0 if token.read(p) else 1
    if a.action == "remove":
        try:
            p.unlink()
            print("removed %s" % p)
        except FileNotFoundError:
            print("no token file")
        return 0
    return 2


def cmd_login_url(a) -> int:
    t = _token_for(a)
    base = a.base_url.rstrip("/") if a.base_url else "http://127.0.0.1:%d" % a.port
    if not t:
        print(base + "/  (no token configured: loopback access needs none)")
        return 0
    print(base + "/?token=" + t)
    return 0


def cmd_status(a) -> int:
    d = paths.data_dir()
    col = paths.find_collector()
    print(json.dumps({"data_dir": str(d), "collector": str(col) if col else None,
                      "has_metrics": (d / "metrics.json").is_file(), "token": bool(token.read(d / "token")),
                      "last_refresh": refresh.state(d)}, indent=1))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="run_portal.py", description="Gnomonkit portal %s" % VERSION)
    ap.add_argument("--data-dir", help="portal data dir (default $GNOMONKIT_PORTAL_DIR, else "
                    "$GNOMONKIT_HOME/portal, else ~/.gnomonkit/portal); goes before the command")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the web server")
    s.add_argument("--host", default="127.0.0.1", help="IP to bind (default 127.0.0.1, this machine only)")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--token-file", help="access token file (default <data dir>/token, used when it exists)")
    s.add_argument("--behind-proxy", action="store_true",
                   help="no token: an AUTHENTICATING reverse proxy in front controls access. Needs --allowed-host. "
                        "Bind loopback or firewall the port to the proxy; never expose the port directly")
    s.add_argument("--allowed-host", action="append", help="accepted Host header (repeatable); required with "
                   "--behind-proxy, set to the public name")
    s.add_argument("--hide-titles", action="store_true", help="never send session titles to the page")
    s.add_argument("--memory", choices=("auto", "on", "off"), help="Memory tab (default auto: on only for a "
                   "loopback bind with no token and no proxy, when a mempalace CLI is found)")
    s.add_argument("--secure-cookie", action="store_true", help="mark the login cookie Secure (behind HTTPS)")
    s.add_argument("--collector", help="path to core's effectiveness.py (default: found automatically)")
    s.add_argument("--quiet", action="store_true")
    s.set_defaults(fn=cmd_serve)
    r = sub.add_parser("refresh", help="run core's collector into the data dir")
    r.add_argument("--since", type=float, help="days to look back (default: config, else 30)")
    r.add_argument("--trigger", default="cli", choices=("cli", "nightly", "button"))
    r.add_argument("--collector")
    r.add_argument("--snapshot", metavar="FILE", help="after a successful refresh, write a static snapshot to FILE "
                   "(replaces only an earlier snapshot)")
    r.add_argument("--hide-titles", action="store_true", help="with --snapshot: leave session titles out of the "
                   "snapshot (the installer passes it when the server hides them)")
    r.add_argument("--trust", action="store_true", help="with --snapshot: include the Trust tab (it names your "
                   "folders, with the home folder as ~)")
    r.set_defaults(fn=cmd_refresh)
    sn = sub.add_parser("snapshot", help="write the page as one self-contained HTML file (no server needed)")
    sn.add_argument("--out", required=True, metavar="FILE")
    sn.add_argument("--force", action="store_true", help="replace FILE when it already exists")
    sn.add_argument("--hide-titles", action="store_true", help="leave session titles out")
    sn.add_argument("--trust", action="store_true", help="include the Trust tab (it names your folders, with the "
                    "home folder as ~)")
    sn.set_defaults(fn=cmd_snapshot)
    t = sub.add_parser("token", help="create, check or remove the access token")
    t.add_argument("action", choices=("init", "check", "remove"))
    t.add_argument("--token-file")
    t.add_argument("--force", action="store_true", help="with init: replace an existing token")
    t.set_defaults(fn=cmd_token)
    lu = sub.add_parser("login-url", help="print the one-time login link (it contains the token)")
    lu.add_argument("--port", type=int, default=DEFAULT_PORT)
    lu.add_argument("--base-url", help="e.g. https://portal.example.com when behind a proxy")
    lu.add_argument("--token-file")
    lu.set_defaults(fn=cmd_login_url)
    st = sub.add_parser("status", help="where things are and how the last refresh went")
    st.set_defaults(fn=cmd_status)
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.data_dir:
        os.environ["GNOMONKIT_PORTAL_DIR"] = a.data_dir      # every path helper reads it from here
    try:
        if sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        return a.fn(a)
    except paths.UnsafePath as e:          # a data folder or file another account could tamper with
        if sys.stderr:
            print(str(e), file=sys.stderr)
        return 2
