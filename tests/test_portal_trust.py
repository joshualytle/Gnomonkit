"""The portal's read-only Trust tab: core's `trust status --json` kept to a whitelist, served in plain loopback
mode only, embedded in a snapshot only when asked. Synthetic data under a made-up home folder only."""
from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "portal"
sys.path.insert(0, str(PLUGIN))

from gnomonkit_portal import cli, server, snapshot, token, trust, view  # noqa: E402

HOME = "/home/" + "demo-user"
WIN_HOME = "C:" + "\\Users\\" + "demo-user"


def status(home=HOME, sep="/", **over):
    """What core's `gnomonkit trust status --json` prints, for a made-up home folder."""
    def p(*parts):
        return sep.join((home,) + parts)
    d = {
        "levels": {"path": p(".gnomonkit", "grants.toml"), "status": "ok", "problems": []},
        "claude_config": {"path": p(".claude.json"), "ok": True, "problem": None},
        "depth": 3, "complete": True, "token": "not-a-field-the-view-keeps",
        "roots": [
            {"root": p("work"), "level": "trusted", "auto": True, "owners": ["demo-org"], "local_only": False,
             "exists": True, "extra": "dropped",
             "repos": [{"repo": p("work", "app"), "trusted": True, "qualifies": True, "reason": None},
                       {"repo": p("work", "new-tool"), "trusted": False, "qualifies": True, "reason": None},
                       {"repo": p("work", "forked"), "trusted": False, "qualifies": False,
                        "reason": "owner other-org is not listed (%s)" % p("work", "forked")},
                       {"repo": p("work", "odd"), "trusted": None, "qualifies": False, "reason": None}]},
            {"root": "/srv/scratch", "level": "per-project", "auto": False, "owners": ["ignored"], "exists": False,
             "repos": [{"repo": "/srv/scratch/try", "trusted": False, "qualifies": False, "reason": "per-project"}]},
            {"root": p("vendor"), "level": "made-up-level", "repos": []},
            "not a root",
        ],
        "uncovered": [p("elsewhere", "one"), p("elsewhere", "two"), "/opt/tool", home],
    }
    d.update(over)
    return d


# ------------------------------------------------------------------ the view
def test_view_keeps_a_whitelist_and_shows_the_home_folder_as_tilde():
    v = view.trust_view(status(), home=HOME)
    blob = view.dumps(v).decode("utf-8")
    assert HOME not in blob
    for gone in ("not-a-field-the-view-keeps", "dropped", "made-up-level", "depth", "ignored"):
        assert gone not in blob, gone
    work, scratch = v["roots"]                                        # an unknown level or a non-object is dropped
    assert work == {"root": "~/work", "level": "trusted", "exists": True, "auto": True, "local_only": False,
                    "owners": ["demo-org"],
                    "repos": [{"name": "app", "state": "trusted"},
                              {"name": "new-tool", "state": "pending"},
                              {"name": "forked", "state": "not trusted",
                               "reason": "owner other-org is not listed (~/work/forked)"},
                              {"name": "odd", "state": "unknown"}]}
    assert scratch == {"root": "/srv/scratch", "level": "per-project", "exists": False,
                       "repos": [{"name": "try", "state": "not trusted", "reason": "per-project"}]}
    assert v["uncovered"] == [{"parent": "~/elsewhere", "folders": ["one", "two"]},
                              {"parent": "/opt", "folders": ["tool"]}, {"parent": "~", "folders": []}]
    assert v["counts"] == {"trusted": 1, "pending": 1, "not_trusted": 2, "unknown": 1, "uncovered": 4}
    assert v["levels"] == {"status": "ok", "path": "~/.gnomonkit/grants.toml", "problems": []}
    assert v["claude_config"] == {"ok": True, "path": "~/.claude.json"}
    assert v["complete"] is True


def test_view_handles_windows_paths_and_cleans_strings():
    v = view.trust_view(status(home=WIN_HOME, sep="\\", levels={
        "path": WIN_HOME + "\\.gnomonkit\\grants.toml", "status": "invalid",
        "problems": ["line 3 of %s\\.gnomonkit\\grants.toml: bad level" % WIN_HOME]}), home=WIN_HOME)
    blob = view.dumps(v).decode("utf-8")
    assert "demo-user" not in blob
    assert v["roots"][0]["root"] == "~/work" and v["roots"][0]["repos"][2]["reason"].endswith("(~\\work\\forked)")
    assert v["levels"]["problems"] == ["line 3 of ~\\.gnomonkit\\grants.toml: bad level"]
    if os.name == "nt":                                               # Windows paths compare without case
        assert view.tilde(WIN_HOME.upper() + "\\Work", view._homes(WIN_HOME)) == "~/Work"
    tricky = status(roots=[{"root": HOME + "/work", "level": "trusted", "owners": ["a\u202eb", 5, ""],
                            "repos": [{"repo": HOME + "/work/app\u202etxt.exe", "trusted": False, "qualifies": False,
                                       "reason": "\x1b[31mred\x1b[0m " + "x" * 400}]}])
    r = view.trust_view(tricky, home=HOME)["roots"][0]
    assert r["owners"] == ["ab"]
    rep = r["repos"][0]
    assert rep["name"] == "apptxt.exe" and "\x1b" not in rep["reason"] and len(rep["reason"]) == 200


def test_view_of_malformed_or_missing_output_is_empty_with_a_note():
    for bad in (None, [], "text", 3):
        v = view.trust_view(bad, home=HOME)
        assert v["roots"] == [] and v["uncovered"] == [] and "could not be read" in v["error"]
    v = view.trust_view({"roots": "x", "uncovered": 5, "levels": [], "claude_config": "y", "complete": False},
                        home=HOME)
    assert v["roots"] == [] and v["uncovered"] == [] and v["complete"] is False
    assert v["levels"]["status"] == "unknown" and v["claude_config"] == {"ok": False, "path": None}
    v = view.trust_view({"roots": [{"root": 7, "level": "trusted"}, {"root": "", "level": "never"},
                                   {"root": "/r", "level": "never", "repos": [3, {"repo": None}, {"repo": "/r/a"}]}]},
                        home=HOME)
    assert v["roots"] == [{"root": "/r", "level": "never", "exists": True,
                           "repos": [{"name": "a", "state": "unknown"}]}]
    view.dumps(v)


# ------------------------------------------------------------------ reading core
def test_read_runs_core_trust_status_and_accepts_only_a_json_object(tmp_path, monkeypatch):
    core_cli = tmp_path / "gnomonkit.py"
    core_cli.write_text("", encoding="utf-8")
    monkeypatch.setenv("GNOMONKIT_CLI", str(core_cli))
    seen = []

    def runner(out="", exc=None, code=0):
        def run(argv, **kw):
            seen.append((argv, kw))
            if exc:
                raise exc
            return SimpleNamespace(returncode=code, stdout=out, stderr="")
        return run

    assert trust.read(runner(json.dumps({"roots": []}), code=1)) == {"roots": []}     # a levels problem exits 1
    argv, kw = seen[0]
    assert argv[1:] == [str(core_cli), "trust", "status", "--json"]
    assert kw["timeout"] == trust.TIMEOUT_S and kw["stdin"] == subprocess.DEVNULL
    for out in ("", None, "not json", "[1, 2]", "null"):
        assert trust.read(runner(out)) is None, out
    assert trust.read(runner(exc=subprocess.TimeoutExpired("core", trust.TIMEOUT_S))) is None
    assert trust.read(runner(exc=OSError("gone"))) is None
    monkeypatch.setenv("GNOMONKIT_CLI", str(tmp_path / "missing.py"))
    n = len(seen)
    assert trust.read(runner("{}")) is None and len(seen) == n                       # no core: nothing runs


def test_allowed_only_in_plain_loopback():
    assert trust.allowed("loopback") is True
    for mode in ("token", "proxy", "snapshot", "LOOPBACK", "", None):
        assert trust.allowed(mode) is False, mode


# ------------------------------------------------------------------ the server
class Live:
    def __init__(self, tmp, reader, **cfg_kw):
        kw = {"refresher": lambda: {"ok": True}, "searcher": lambda q, c: {"results": []}, "mem_cli": "",
              "trust_reader": reader}
        cfg_kw.setdefault("work_file", tmp / "work-items.json")
        cfg = server.Config(port=0, data_dir=tmp / "portal", **cfg_kw)
        self.srv = server.make_server(cfg, **kw)
        self.port = self.srv.server_address[1]
        self.origin = {"Origin": "http://127.0.0.1:%d" % self.port, "Host": "127.0.0.1:%d" % self.port}
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def req(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class Reader:
    def __init__(self, result=None, delay=0.0, exc=None):
        self.calls, self.result, self.delay, self.exc = 0, result, delay, exc

    def __call__(self):
        self.calls += 1
        time.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A made-up home folder, so the view's ~ applies to the synthetic data and nothing reads the real one."""
    monkeypatch.setenv("HOME", HOME)
    monkeypatch.setenv("USERPROFILE", HOME)
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    monkeypatch.setenv("GNOMONKIT_PORTAL_DIR", str(tmp_path / "portal"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("GNOMONKIT_CLI", raising=False)


def test_api_trust_in_loopback_is_read_once_per_cache_window(tmp_path, monkeypatch):
    rd = Reader(status())
    lv = Live(tmp_path, rd)
    try:
        assert json.loads(lv.req("GET", "/api/status")[1])["trust"] is True
        st, body = lv.req("GET", "/api/trust")
        assert st == 200 and HOME.encode() not in body and b"<" not in body
        v = json.loads(body)
        assert v["roots"][0]["root"] == "~/work" and v["counts"]["pending"] == 1
        assert lv.req("GET", "/api/trust")[0] == 200 and rd.calls == 1               # served from the cache
        monkeypatch.setattr(trust, "CACHE_S", 0)
        assert lv.req("GET", "/api/trust")[0] == 200 and rd.calls == 2
        for method in ("POST", "PUT", "DELETE"):                                      # read-only: nothing to post
            assert lv.req(method, "/api/trust", lv.origin, b"{}")[0] in (404, 405), method
        assert rd.calls == 2
    finally:
        lv.close()


def test_api_trust_runs_one_read_at_a_time(tmp_path):
    rd = Reader(status(), delay=0.4)
    lv = Live(tmp_path, rd)
    try:
        got = []
        ts = [threading.Thread(target=lambda: got.append(lv.req("GET", "/api/trust")[0])) for _ in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        assert got == [200, 200, 200] and rd.calls == 1                 # the reloads waited for the one read
    finally:
        lv.close()


@pytest.mark.parametrize("reader", [Reader(None), Reader(exc=RuntimeError("boom")), Reader(["not", "an object"])])
def test_api_trust_failure_is_a_note_never_a_500(tmp_path, reader):
    lv = Live(tmp_path, reader)
    try:
        st, body = lv.req("GET", "/api/trust")
        v = json.loads(body)
        assert st == 200 and v["roots"] == [] and "could not be read" in v["error"] and b"boom" not in body
    finally:
        lv.close()


def test_api_trust_is_404_with_a_token_or_behind_a_proxy(tmp_path):
    tok = token.generate()
    rd = Reader(status())
    lv = Live(tmp_path, rd, token=tok)
    try:
        auth = {server.TOKEN_HEADER: tok}
        assert lv.req("GET", "/api/metrics", auth)[0] == 200
        assert lv.req("GET", "/api/trust", auth)[0] == 404
        assert json.loads(lv.req("GET", "/api/status", auth)[1])["trust"] is False
    finally:
        lv.close()
    lv = Live(tmp_path, rd, behind_proxy=True, allowed_hosts={"portal.example.com"})
    try:
        host = {"Host": "portal.example.com"}
        assert lv.req("GET", "/api/metrics", host)[0] == 200
        assert lv.req("GET", "/api/trust", host)[0] == 404
    finally:
        lv.close()
    assert rd.calls == 0


def test_page_has_a_trust_tab_that_writes_no_markup():
    page = server.PAGE_FILE.read_text(encoding="utf-8")
    assert 'id="t-trust"' in page and 'id="p-trust"' in page and "/api/trust" in page
    fn = re.search(r"function renderTrust\(.*?\n}\n", page, re.S).group(0)
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
        assert bad not in fn


# ------------------------------------------------------------------ snapshots
def _data(tmp_path):
    d = tmp_path / "data"
    d.mkdir(mode=0o700)
    (d / "metrics.json").write_text(json.dumps({"schema": 1, "sections": {}}), encoding="utf-8")
    return d


def _blob(html):
    return json.loads(re.search(r'id="gk-snapshot">(.*?)</script>', html, re.S).group(1))


def test_snapshot_holds_trust_only_when_asked(tmp_path):
    d = _data(tmp_path)
    rd = Reader(status())
    blob = _blob(snapshot.build(d, trust_reader=rd))
    assert "trust" not in blob and blob["status"]["trust"] is False and rd.calls == 0
    html = snapshot.build(d, with_trust=True, trust_reader=rd)
    blob = _blob(html)
    assert rd.calls == 1 and blob["status"]["trust"] is True and HOME not in html
    assert blob["trust"]["roots"][0]["root"] == "~/work" and blob["trust"]["uncovered"][0]["parent"] == "~/elsewhere"
    assert "connect-src 'none'" in html                               # still makes no request


def test_cli_snapshot_trust_flag(tmp_path, monkeypatch, capsys):
    d = _data(tmp_path)
    monkeypatch.setenv("GNOMONKIT_PORTAL_DIR", str(d))
    rd = Reader(status())
    monkeypatch.setattr(trust, "read", rd)                           # never the real core or Claude Code file
    out = tmp_path / "portal.html"
    assert cli.main(["snapshot", "--out", str(out)]) == 0
    assert "folder-trust" not in capsys.readouterr().out and "trust" not in _blob(out.read_text(encoding="utf-8"))
    assert cli.main(["snapshot", "--out", str(out), "--force", "--trust"]) == 0
    assert "folder-trust" in capsys.readouterr().out and rd.calls == 1
    assert _blob(out.read_text(encoding="utf-8"))["trust"]["counts"]["trusted"] == 1
    col = tmp_path / "fake_collector.py"
    col.write_text("import sys\nout = sys.argv[sys.argv.index('--json') + 1]\n"
                   "open(out, 'w', encoding='utf-8').write('{\"schema\": 1, \"sections\": {}}')\n", encoding="utf-8")
    nightly = tmp_path / "nightly.html"
    assert cli.main(["refresh", "--collector", str(col), "--snapshot", str(nightly)]) == 0
    assert "trust" not in _blob(nightly.read_text(encoding="utf-8")) and rd.calls == 1
    assert cli.main(["refresh", "--collector", str(col), "--snapshot", str(nightly), "--trust"]) == 0
    assert "trust" in _blob(nightly.read_text(encoding="utf-8")) and rd.calls == 2
