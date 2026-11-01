"""`run_portal.py snapshot`: the page as one self-contained HTML file that needs no server.

For hosts that serve static files only, or for opening the dashboard from disk. The file is the live
page with the public view (view.py, the same whitelist the API serves) embedded as JSON in a
<script type="application/json"> block, so it shows exactly what the live page shows and nothing more.
It never includes the Memory tab, a search result, the refresh button or a sign-out link. The Trust tab
(trust.py) names the user's folders, so it is embedded only when asked for with --trust.

The embedded JSON escapes <, > and & (view.dumps), so no title can close its script element. A CSP
meta tag allows only this file's own script and style, by hash, and connect-src 'none' forbids every
request, so the page cannot fetch, post or beacon anything even if a value were somehow executed.

The file is written through a fresh mkstemp name and os.replace (paths.write_private), readable by its
owner only. An existing file is replaced only with --force, or, for the nightly refresh's
--snapshot, when it is a snapshot this command wrote earlier. Replacing a snapshot keeps the read
access its owner gave it (at most 0644), so a web server that was allowed to read it still can.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
import time
from pathlib import Path

from . import VERSION, paths, refresh, server, trust, view

MARKER = '<meta name="generator" content="gnomonkit-portal snapshot">'
_BLOCK = re.compile(r"[ \t]*<!--gk:(memory|live)-->.*?<!--/gk:\1-->\n?", re.S)
_SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)
_STYLE = re.compile(r"<style>(.*?)</style>", re.S)


class SnapshotRefused(paths.UnsafePath):
    """The output path holds something this command will not replace."""


def _sha256(text: str) -> str:
    return "'sha256-%s'" % base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")


def csp_for(script: str, style: str) -> str:
    return ("default-src 'none'; script-src %s; style-src %s; connect-src 'none'; img-src data:; "
            "base-uri 'none'; form-action 'none'" % (_sha256(script), _sha256(style)))


def data_for(d: Path, hide_titles: bool, now: float | None = None, with_trust: bool = False,
             trust_reader=None) -> dict:
    """{status, metrics[, trust]}: what the live page would fetch from /api/status and /api/metrics, minus
    anything that only makes sense on a server (memory, refresh controls, the sign-in mode). The trust view
    only when asked for."""
    m = paths.read_json(d / "metrics.json", {})
    try:
        v = view.public_view(m, hide_titles)
        view.dumps(v)
    except Exception:
        v = view.public_view({}, hide_titles)
    if not m or not isinstance(m, dict):
        v["empty"] = True
    st = refresh.state(d)
    status = {"version": VERSION, "auth": "snapshot", "memory": False, "snapshot": True,
              "snapshot_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time())),
              "has_data": bool(m), "hide_titles": bool(hide_titles), "trust": bool(with_trust),
              "refresh": {k: server._scalar(st.get(k)) for k in ("trigger", "ok", "error", "finished_at", "last_ok_at")
                          if k in st}}
    out = {"status": status, "metrics": v}
    if with_trust:
        out["trust"] = view.trust_view((trust_reader or trust.read)())
    return out


def build(d: Path | None = None, hide_titles: bool = False, now: float | None = None, with_trust: bool = False,
          trust_reader=None) -> str:
    d = d or paths.data_dir()
    page = server.PAGE_FILE.read_text(encoding="utf-8")
    html = _BLOCK.sub("", page).replace(' nonce="__NONCE__"', "")
    if "__NONCE__" in html or "<!--gk:" in html.replace("<!--gk:data-->", ""):
        raise RuntimeError("page.html no longer has the markers the snapshot needs")
    script, style = _SCRIPT.search(html).group(1), _STYLE.search(html).group(1)
    blob = view.dumps(data_for(d, hide_titles, now, with_trust, trust_reader)).decode("utf-8")
    html = html.replace("<!--gk:data-->", '<script type="application/json" id="gk-snapshot">%s</script>' % blob, 1)
    head = '<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" content="%s">\n%s' % (
        csp_for(script, style), MARKER)
    return html.replace('<meta charset="utf-8">', head, 1)


def is_snapshot(p: Path) -> bool:
    try:
        with open(p, "rb") as fh:
            return MARKER.encode("ascii") in fh.read(4096)
    except OSError:
        return False


def write(out: Path, d: Path | None = None, hide_titles: bool = False, force: bool = False,
          replace_snapshot: bool = False, now: float | None = None, with_trust: bool = False,
          trust_reader=None) -> Path:
    """Write the snapshot to out. Raises SnapshotRefused rather than replace a file it should not."""
    out = Path(out)
    if out.name in ("", ".", ".."):
        raise SnapshotRefused("give a file name for the snapshot, not a folder")
    if not out.parent.is_dir():
        raise SnapshotRefused("the folder for %s does not exist" % out)
    keep_mode = None
    if os.path.lexists(out):
        st = os.lstat(out)
        if stat.S_ISDIR(st.st_mode):
            raise SnapshotRefused("%s is a folder" % out)
        if not (force or (replace_snapshot and not stat.S_ISLNK(st.st_mode) and is_snapshot(out))):
            raise SnapshotRefused("%s already exists; pass --force to replace it" % out)
        if stat.S_ISREG(st.st_mode) and is_snapshot(out):
            keep_mode = stat.S_IMODE(st.st_mode) & 0o644
    html = build(d, hide_titles, now, with_trust, trust_reader)
    # 0600 through mkstemp, then os.replace. A kept mode is set on the temp file before the swap: a chmod of
    # out by name afterwards could follow a symlink swapped in meanwhile.
    paths.write_private(out, html, mode=(keep_mode | 0o600) if keep_mode else 0o600)
    return out
