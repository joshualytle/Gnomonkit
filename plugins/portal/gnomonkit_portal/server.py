"""The portal's HTTP server: standard library only, read-only, one page plus a small JSON API.

ACCESS. Three modes, picked at start and fixed for the process:

  loopback   bound to 127.0.0.1 / ::1 with no token. Anyone on this machine can read the page, so the
             Host header must name a loopback host (a DNS-rebinding page cannot pass that check).
  token      an access token is configured (always, when a token file exists). Every request but
             /healthz carries it: the X-Gnomonkit-Token header, Authorization: Bearer, or the
             HttpOnly SameSite=Strict session cookie set after a one-time /?token=... login, which then
             redirects to a clean URL so the token does not stay in the address bar or history. The
             cookie never holds the token: it holds the time it was issued and an HMAC of that time
             keyed by the token, so it expires on the server's clock (SESSION_MAX_AGE), /logout clears
             it, and a new token ends every session.
  proxy      --behind-proxy: an authenticating reverse proxy in front does access control. The
             portal trusts every request that reaches it, so bind it to loopback (proxy on the same
             host) or firewall it to the proxy. --allowed-host is required: it names the public host,
             and every other Host header is refused, so a DNS-rebinding page cannot reach the API.

A non-loopback bind without a token or --behind-proxy refuses to start, and so does --behind-proxy
without --allowed-host.

READ-ONLY. GET serves the page, /api/status, /api/metrics (view.py's whitelisted view, never the
metrics file itself), /api/sync (updates.py's whitelisted view of what the sync gate holds), /api/work
(view.work_view over core's work-item summary: states, ages and links, titles unless hidden, never an
issue body), /api/trust in loopback mode only (trust.allowed: view.trust_view over core's
`gnomonkit trust status --json`, the folder-trust levels with the home folder shown as ~) and, when
enabled, /api/memory. POST /api/refresh needs the token in a header or a
same-origin request (Origin matching Host, or Sec-Fetch-Site: same-origin), is rate-limited, and only
starts the collector. POST /api/sync/approve (the Draft approval button) exists in loopback mode only
(approval_allowed): it needs a same-origin JSON request naming an update the gate holds, and runs
core's `gnomonkit sync approve-draft`, which opens a pull request on the user's overlay. Merging that
pull request, never this server, is the approval. Every other method is refused. Cross-site requests
(Sec-Fetch-Site: cross-site) are refused outright, and nothing sends CORS headers.

LIMITS. At most MAX_CONNECTIONS connections at once (the next gets a 503 and is closed), an idle
socket times out after TIMEOUT seconds, and a request body must have a plain decimal Content-Length
of at most 1024 bytes (anything else is a 400 or 413 and the connection is closed).

The page carries a per-response CSP nonce; every response, error pages included, has nosniff, no
framing, no referrer and no-store. The access log records method, path and status only: never a
query string, which is where a login token travels.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import logging.handlers
import math
import os
import re
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import VERSION, memory, paths, refresh, trust, updates, view

PAGE_FILE = Path(__file__).with_name("page.html")
# 7451: no well-known service uses it, and it sits below the dynamic range Windows reserves port blocks
# from. 8765 (the first default) is taken by common desktop software.
DEFAULT_PORT = 7451
HEALTH_BODY = "gnomonkit-portal"
HEALTH_HEADER = "X-Gnomonkit-Portal"
COOKIE = "gk_portal"
TOKEN_HEADER = "X-Gnomonkit-Token"
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", "[::1]"}
SESSION_MAX_AGE = 7 * 86400
MAX_CONNECTIONS = 32
TIMEOUT = 10
BODY_MAX = 1024
_LENGTH = re.compile(r"[0-9]{1,7}")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
API_CSP = "default-src 'none'; frame-ancestors 'none'"

log = logging.getLogger("gnomonkit.portal")


class StartRefused(SystemExit):
    pass


def is_loopback(host: str) -> bool:
    h = (host or "").strip().strip("[]")
    if h == "localhost":
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def check_start(host: str, token: str | None, behind_proxy: bool, allowed_hosts=()) -> str:
    """The access mode for this bind, or StartRefused. A name other than localhost must be an IP literal,
    so what is bound is exactly what was asked for."""
    h = (host or "").strip().strip("[]")
    if h != "localhost":
        try:
            ipaddress.ip_address(h)
        except ValueError:
            raise StartRefused("refusing to bind %r: give an IP address (or localhost), not a host name" % host)
    if token:
        return "token"
    if behind_proxy:
        if not any(isinstance(a, str) and a.strip() for a in allowed_hosts or ()):
            raise StartRefused(
                "refusing --behind-proxy without --allowed-host: name the public host the proxy serves "
                "(--allowed-host portal.example.com). Without it any Host header is accepted, so a page that "
                "rebinds its own DNS name to this address could read the API.")
        return "proxy"
    if is_loopback(h):
        return "loopback"
    raise StartRefused(
        "refusing to bind %s without access control. Either create a token (portal token init, then start "
        "again: the token file is picked up automatically) or, only when an authenticating reverse proxy "
        "sits in front, pass --behind-proxy with --allowed-host." % host)


def session_value(tok: str, issued: int) -> str:
    """The cookie value: when it was issued, and an HMAC of that time keyed by the token."""
    mac = hmac.new(tok.encode("utf-8"), b"gnomonkit-portal session %d" % issued, hashlib.sha256).hexdigest()
    return "%d.%s" % (issued, mac)


def session_ok(tok: str, value: bytes, now: float | None = None) -> bool:
    if not tok or not value:
        return False
    issued_s, _, _ = value.partition(b".")
    if not re.fullmatch(rb"[0-9]{1,12}", issued_s):
        return False
    issued = int(issued_s)
    age = (time.time() if now is None else now) - issued
    if age < -300 or age > SESSION_MAX_AGE:          # a few minutes of clock skew, no more
        return False
    return hmac.compare_digest(value, session_value(tok, issued).encode("ascii"))


def approval_allowed(server_mode) -> bool:
    """Whether the Draft approval button and its endpoint exist. Only plain loopback: the person at this
    machine. Behind a token or a proxy the page may be open on another device, and drafting an approval
    from there is a remote write to the user's overlay; any mode not named here is refused too."""
    return server_mode == "loopback"


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    token: str | None = None
    behind_proxy: bool = False
    allowed_hosts: set = field(default_factory=set)
    data_dir: Path = field(default_factory=paths.data_dir)
    hide_titles: bool = False
    memory: str = "auto"                  # auto | on | off
    refresh_min_interval: int = 600
    secure_cookie: bool = False
    collector: Path | None = None
    max_connections: int = MAX_CONNECTIONS
    work_file: Path | None = None         # None: core's summary under $GNOMONKIT_HOME (paths.work_file)


def _scalar(v):
    """A state.json value fit for the page: a short clean string, a bool, a finite number, or None."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v if math.isfinite(v) else None
    return view.text(v, 200)


class State:
    """What the handler threads share: the cached view, the refresh gate, the memory switch."""

    def __init__(self, cfg: Config, refresher=None, searcher=None, mem_cli=None, mem_index=None,
                 index_searcher=None, drafter=None, sync_reader=None, trust_reader=None):
        self.cfg = cfg
        self.mode = check_start(cfg.host, cfg.token, cfg.behind_proxy, cfg.allowed_hosts)
        self.lock = threading.Lock()
        self.draft_lock = threading.Lock()
        self.drafter = drafter or updates.draft
        self.sync_reader = sync_reader or updates.public_view
        self.trust_reader = trust_reader or trust.read
        self.trust_lock = threading.Lock()
        self._trust = (None, None)
        self._view = (None, None)
        self.last_button = 0.0
        self.refresher = refresher or (lambda: refresh.run("button", d=cfg.data_dir, collector=cfg.collector))
        self.searcher = searcher or memory.search
        self.index_searcher = index_searcher or memory.search_index
        self.mem_cli = mem_cli if mem_cli is not None else memory.find_cli()
        # None: look for core's history index on each status check (it may be built after the portal
        # starts); "" never use one; a path: that file.
        self._mem_index = mem_index
        if cfg.memory == "off":
            self.mem_allowed = False
        elif cfg.memory == "on":
            self.mem_allowed = True
        else:                             # auto: on only in plain loopback mode (this machine, no token, no proxy)
            self.mem_allowed = self.mode == "loopback"
        hosts = {h.strip().lower() for h in cfg.allowed_hosts if isinstance(h, str) and h.strip()}
        if self.mode == "loopback":
            hosts |= LOOPBACK_NAMES
        self.allowed_hosts = hosts

    @property
    def mem_index(self) -> str:
        if self._mem_index is not None:
            return self._mem_index if (not self._mem_index or os.path.isfile(self._mem_index)) else ""
        return memory.find_index() or ""

    @property
    def memory_source(self) -> str | None:
        """"mempalace" (a CLI was found), "history" (core's local index) or None (the tab is off)."""
        if not self.mem_allowed:
            return None
        if self.mem_cli:
            return "mempalace"
        return "history" if self.mem_index else None

    @property
    def memory(self) -> bool:
        return self.memory_source is not None

    def memory_search(self, q: str) -> dict:
        src = self.memory_source
        if src == "mempalace":
            return self.searcher(q, self.mem_cli)
        return self.index_searcher(q, self.mem_index)

    def _empty_view(self, note: str | None = None) -> dict:
        v = view.public_view({}, self.cfg.hide_titles) | {"empty": True}
        if note:
            v["error"] = note
        return v

    def metrics_view(self) -> dict:
        p = self.cfg.data_dir / "metrics.json"
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            return self._empty_view()
        with self.lock:
            if self._view[0] == mt:
                return self._view[1]
        m = paths.read_json(p, {})
        try:
            v = view.public_view(m, self.cfg.hide_titles)
            view.dumps(v)                 # must encode: a value JSON cannot carry fails here, not mid-response
            if not m or not isinstance(m, dict):
                v["empty"] = True
        except Exception as e:            # a malformed file gives an empty page, never a broken API
            log.warning("metrics.json does not fit the view (%s); serving an empty one", type(e).__name__)
            v = self._empty_view("the metrics file could not be read; run a refresh")
        with self.lock:
            self._view = (mt, v)
        return v

    def work_view(self) -> dict:
        """Read on every request (the file is small and ages move with the clock); never the file as is."""
        p = self.cfg.work_file or paths.work_file()
        data = paths.read_json(p, None)
        v = view.work_view(data or {}, self.cfg.hide_titles)
        if not isinstance(data, dict):
            v["empty"] = True
        return v

    def status(self) -> dict:
        st = refresh.state(self.cfg.data_dir)
        wait = self.retry_after()
        return {
            "version": VERSION, "auth": self.mode, "memory": self.memory, "memory_source": self.memory_source,
            "has_data": (self.cfg.data_dir / "metrics.json").is_file(),
            "refresh": {"running": refresh.running(self.cfg.data_dir),
                        **{k: _scalar(st.get(k)) for k in ("trigger", "ok", "error", "started_at", "finished_at",
                                                           "last_ok_at", "seconds") if k in st},
                        "min_interval": self.cfg.refresh_min_interval, "retry_after": wait},
            "hide_titles": self.cfg.hide_titles,
            "sync_approval": approval_allowed(self.mode),
            "trust": trust.allowed(self.mode),
        }

    def sync_view(self) -> dict:
        try:
            v = self.sync_reader()
        except Exception as e:                       # an unreadable gate folder shows as empty, never a 500
            log.warning("sync view failed: %s", type(e).__name__)
            v = {"sources": [], "held": [], "overlay": False, "checked_at": None, "unreadable": True}
        return v | {"approval": approval_allowed(self.mode)}

    def trust_view(self) -> dict:
        """Core's trust status, read at most once per trust.CACHE_S and one read at a time: a reload waits
        for the read in progress and gets its answer instead of starting another scan."""
        with self.trust_lock:
            at, v = self._trust
            if at is not None and time.monotonic() - at < trust.CACHE_S:
                return v
            try:
                v = view.trust_view(self.trust_reader())
                view.dumps(v)                        # must encode, as the metrics view must
            except Exception as e:                   # a failing read shows a note on the tab, never a 500
                log.warning("trust view failed: %s", type(e).__name__)
                v = view.trust_view(None)
            self._trust = (time.monotonic(), v)
            return v

    def draft_approval(self, hid: str) -> tuple[int, dict]:
        if hid not in updates.offered_ids(self.sync_view()):
            return 404, {"error": "no held update with that id"}
        if not self.draft_lock.acquire(blocking=False):
            return 409, {"error": "a draft is already being made"}
        try:
            code, body = self.drafter(hid)
            log.info("sync approve-draft: %s", code)
            return code, body
        except Exception as e:
            log.warning("sync approve-draft failed: %s", type(e).__name__)
            return 500, {"error": "the draft failed (%s)" % type(e).__name__}
        finally:
            self.draft_lock.release()

    def retry_after(self) -> int:
        return max(0, int(self.last_button + self.cfg.refresh_min_interval - time.time()))

    def start_refresh(self) -> tuple[int, dict]:
        with self.lock:
            if refresh.running(self.cfg.data_dir):
                return 409, {"error": "a refresh is already running"}
            wait = self.retry_after()
            if wait > 0:
                return 429, {"error": "refreshed recently", "retry_after": wait}
            self.last_button = time.time()
        threading.Thread(target=self._run_refresh, name="portal-refresh", daemon=True).start()
        return 202, {"started": True}

    def _run_refresh(self):
        try:
            rec = self.refresher()
            log.info("refresh (button): %s", "ok" if rec.get("ok") else rec.get("error"))
        except Exception as e:                       # a failed refresh never takes the server down
            log.warning("refresh (button) failed: %s", type(e).__name__)


def _host_only(h: str) -> str:
    h = (h or "").strip().lower()
    if h.startswith("["):
        return h.split("]")[0] + "]"
    return h.rsplit(":", 1)[0] if h.count(":") == 1 else h


def make_handler(st: State, page: str):
    tok_s = st.cfg.token or ""
    tok = tok_s.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        server_version = "gnomonkit-portal"
        sys_version = ""
        protocol_version = "HTTP/1.1"
        timeout = TIMEOUT

        def log_message(self, *a):
            pass

        def _access(self, code: int):
            log.info("%s %s %s", getattr(self, "command", "") or "-", urlsplit(getattr(self, "path", "") or "").path[:80],
                     code)

        # ---- responses
        def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            self._access(code)

        def _json(self, obj, code: int = 200, extra: dict | None = None):
            self._send(code, view.dumps(obj), "application/json; charset=utf-8",
                       {"Content-Security-Policy": API_CSP, **(extra or {})})

        def _bare(self, code: int, extra: dict | None = None):
            self._send(code, b"", "text/plain; charset=utf-8", {"Content-Security-Policy": API_CSP, **(extra or {})})

        def _close(self, code: int):
            """An error after which the rest of the request cannot be trusted: answer and drop the connection."""
            self._bare(code, {"Connection": "close"})

        def parse_request(self):
            """HTTP/0.9 (a request line with no version) is refused: its responses carry no status line and
            no headers, so neither the CSP nor any other security header could be sent."""
            ok = super().parse_request()
            if ok and self.request_version == "HTTP/0.9":
                self.send_error(400)
                return False
            return ok

        def send_error(self, code, message=None, explain=None):
            """http.server's own errors (a malformed request line, an over-long header, a timeout) get the same
            headers as every other response, a short plain body, and never echo the request back."""
            self.close_connection = True
            if getattr(self, "request_version", "") == "HTTP/0.9":
                self.request_version = ""      # Python 3.12 answers a bad request line HTTP/0.9-style, headerless
            try:
                short = self.responses.get(code, ("Error",))[0]
            except Exception:
                short = "Error"
            body = ("%d %s\n" % (code, short)).encode("ascii", "replace")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            for k, v in {**SECURITY_HEADERS, "Content-Security-Policy": API_CSP, "Connection": "close"}.items():
                self.send_header(k, v)
            send_body = code >= 200 and code not in (204, 205, 304) and getattr(self, "command", "") != "HEAD"
            self.send_header("Content-Length", str(len(body) if send_body else 0))
            self.end_headers()
            if send_body:
                self.wfile.write(body)
            self._access(code)

        # ---- gates
        def _presented(self) -> bytes:
            h = self.headers.get(TOKEN_HEADER) or ""
            if not h:
                a = self.headers.get("Authorization") or ""
                if a[:7].lower() == "bearer ":
                    h = a[7:].strip()
            return h.encode("utf-8", "replace")

        def _cookie(self) -> bytes:
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == COOKIE:
                    return v.strip().encode("utf-8", "replace")
            return b""

        def _header_token_ok(self) -> bool:
            return bool(tok) and hmac.compare_digest(self._presented(), tok)

        def _authorized(self) -> bool:
            if st.mode != "token":
                return True
            return self._header_token_ok() or session_ok(tok_s, self._cookie())

        def _same_origin(self) -> bool:
            if self.headers.get("Sec-Fetch-Site") == "same-origin":
                return True
            origin = self.headers.get("Origin") or ""
            if not origin:
                return False
            o = urlsplit(origin)
            return bool(o.netloc) and o.netloc.lower() == (self.headers.get("Host") or "").lower()

        def _pre(self) -> bool:
            """Checks every request passes before routing. False means a response was already sent."""
            if len(self.path) > 2048:
                self._bare(414)
                return False
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                self._bare(403)
                return False
            # /healthz is exempt: it shows only the version, and the installer probes it by IP behind a proxy.
            if st.allowed_hosts and urlsplit(self.path).path != "/healthz" \
                    and _host_only(self.headers.get("Host") or "") not in st.allowed_hosts:
                self._bare(421)                       # DNS rebinding, or a name nobody configured
                return False
            return True

        def _secure(self) -> str:
            return "; Secure" if (st.cfg.secure_cookie or
                                  self.headers.get("X-Forwarded-Proto", "").lower() == "https") else ""

        # ---- methods
        def do_GET(self):
            if not self._pre():
                return
            u = urlsplit(self.path)
            q = parse_qs(u.query)
            if u.path == "/healthz":
                # A signature, not just "ok": the installer checks it to tell the portal from any other program
                # that happens to answer on the port. It reveals the version and nothing else.
                return self._send(200, (HEALTH_BODY + " " + VERSION).encode("ascii"), "text/plain; charset=utf-8",
                                  {HEALTH_HEADER: VERSION})
            if u.path in ("/", "/index.html") and "token" in q and st.mode == "token":
                given = (q.get("token") or [""])[0].encode("utf-8", "replace")
                if not hmac.compare_digest(given, tok):
                    return self._send(401, LOGIN_FAILED, "text/html; charset=utf-8",
                                      {"Content-Security-Policy": API_CSP})
                cookie = "%s=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d%s" % (
                    COOKIE, session_value(tok_s, int(time.time())), SESSION_MAX_AGE, self._secure())
                return self._bare(303, {"Location": "/", "Set-Cookie": cookie})
            if u.path == "/logout":
                # Clears the session cookie. Clearing is harmless, so it needs no token; a new token ends every session.
                cookie = "%s=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0%s" % (COOKIE, self._secure())
                return self._bare(303, {"Location": "/", "Set-Cookie": cookie})
            if not self._authorized():
                if u.path in ("/", "/index.html"):
                    return self._send(401, LOGIN_NEEDED, "text/html; charset=utf-8",
                                      {"Content-Security-Policy": API_CSP})
                return self._bare(401)
            if u.path in ("/", "/index.html"):
                nonce = secrets.token_urlsafe(16)
                csp = ("default-src 'none'; script-src 'nonce-%s'; style-src 'nonce-%s'; connect-src 'self'; "
                       "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
                       % (nonce, nonce))
                return self._send(200, page.replace("__NONCE__", nonce).encode("utf-8"), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": csp})
            if u.path == "/favicon.ico":
                return self._bare(204)
            if u.path == "/api/status":
                return self._json(st.status())
            if u.path == "/api/metrics":
                return self._json(st.metrics_view())
            if u.path == "/api/sync":
                return self._json(st.sync_view())
            if u.path == "/api/work":
                return self._json(st.work_view())
            if u.path == "/api/trust":
                if not trust.allowed(st.mode):
                    return self._bare(404)
                return self._json(st.trust_view())
            if u.path == "/api/memory":
                if not st.memory:
                    return self._bare(404)
                return self._json(st.memory_search((q.get("q") or [""])[0]))
            return self._bare(404)

        def _body_length(self) -> int | None:
            """The request body's length, or None after refusing a body this server will not read."""
            if self.headers.get("Transfer-Encoding"):
                self._close(400)                      # no chunked bodies: the one POST needs none
                return None
            vals = self.headers.get_all("Content-Length") or []
            if not vals:
                return 0
            if len(set(v.strip() for v in vals)) != 1 or not _LENGTH.fullmatch(vals[0].strip()):
                self._close(400)                      # negative, signed, non-numeric, huge or conflicting
                return None
            n = int(vals[0].strip())
            if n > BODY_MAX:
                self._close(413)
                return None
            return n

        def do_POST(self):
            if not self._pre():
                return
            n = self._body_length()
            if n is None:
                return
            body = self.rfile.read(n) if n else b""     # read once, here: no handler reads rfile again
            u = urlsplit(self.path)
            if not self._authorized():
                return self._bare(401)
            if u.path == "/api/sync/approve":
                return self._sync_approve(body)
            if u.path != "/api/refresh":
                return self._bare(404)
            if not (self._header_token_ok() or self._same_origin()):
                return self._bare(403)
            code, body = st.start_refresh()
            extra = {"Retry-After": str(body["retry_after"])} if code == 429 else None
            return self._json(body, code, extra)

        def _sync_approve(self, raw: bytes):
            # Not offered at all outside plain loopback; then only a same-origin JSON request from the page.
            # A token in a header does not stand in for same-origin here: the button is for the person at
            # this machine. `raw` is the body do_POST read after _body_length() checked its length (a bad
            # length is a 400, over BODY_MAX a 413, before any route).
            if not approval_allowed(st.mode):
                return self._bare(404)
            if not self._same_origin():
                return self._bare(403)
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                return self._bare(415)
            if not raw:
                return self._bare(400)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return self._json({"error": "not JSON"}, 400)
            hid = body.get("id") if isinstance(body, dict) else None
            if not isinstance(hid, str) or not updates.ID.match(hid):
                return self._json({"error": "not an update id"}, 400)
            code, out = st.draft_approval(hid)
            return self._json(out, code)

        def _refuse(self):
            if not self._pre():
                return
            self._bare(405, {"Allow": "GET, POST", "Connection": "close"})     # any body is left unread

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _refuse

        def do_HEAD(self):
            self._refuse()

    return Handler


LOGIN_NEEDED = (b"<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><title>Sign in needed</title>"
                b"<body style=\"font:16px system-ui,sans-serif;max-width:40em;margin:3em auto;padding:0 16px\">"
                b"<h1>Sign in needed</h1><p>This portal needs its access token. Open the login link once; "
                b"print it on the machine that runs the portal with <code>run_portal.py login-url</code>.</p>")
LOGIN_FAILED = (b"<!doctype html><html lang=\"en\"><meta charset=\"utf-8\"><title>Wrong token</title>"
                b"<body style=\"font:16px system-ui,sans-serif;max-width:40em;margin:3em auto;padding:0 16px\">"
                b"<h1>That token is not right</h1><p>Print a fresh login link with "
                b"<code>run_portal.py login-url</code>.</p>")
BUSY = (b"HTTP/1.1 503 Service Unavailable\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: 5\r\n"
        b"Retry-After: 5\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\n"
        b"\r\nbusy\n")


class PortalHTTPServer(ThreadingHTTPServer):
    """On Windows, SO_REUSEADDR (http.server's default) lets a second process bind the same port and
    take the portal's traffic. There the bind is exclusive instead; POSIX keeps SO_REUSEADDR, which
    only allows a quick restart over TIME_WAIT.

    One thread per connection, at most max_connections of them: a client that opens sockets and never
    finishes a request can hold a slot only until the socket timeout, and cannot exhaust threads."""
    allow_reuse_address = os.name != "nt"
    max_connections = MAX_CONNECTIONS

    def __init__(self, *a, max_connections: int | None = None, **kw):
        if max_connections:
            self.max_connections = max_connections
        self._slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(*a, **kw)

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(BUSY)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class PortalHTTPServer6(PortalHTTPServer):
    address_family = socket.AF_INET6


def make_server(cfg: Config, **kw) -> ThreadingHTTPServer:
    st = State(cfg, **kw)
    page = PAGE_FILE.read_text(encoding="utf-8")
    host = cfg.host.strip("[]")
    cls = PortalHTTPServer6 if ":" in host else PortalHTTPServer
    srv = cls((host, cfg.port), make_handler(st, page), max_connections=cfg.max_connections)
    srv.daemon_threads = True
    srv.portal_state = st
    return srv


class PrivateLogHandler(logging.handlers.RotatingFileHandler):
    """A rotating log opened 0600 and never through a symlink (paths.open_private_append)."""

    def _open(self):
        return paths.open_private_append(Path(self.baseFilename), self.encoding or "utf-8")


def setup_logging(d: Path, to_stderr: bool) -> None:
    paths.private_dir(d)
    log.setLevel(logging.INFO)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in log.handlers):
        fh = PrivateLogHandler(d / "portal.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    if to_stderr and not any(type(h) is logging.StreamHandler for h in log.handlers):
        log.addHandler(logging.StreamHandler())
