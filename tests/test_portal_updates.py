"""Portal: the Updates tab for the sync gate (docs/SYNC.md). The whitelisted /api/sync view, the Draft approval
endpoint that exists only in plain loopback mode, the call into core's CLI, and the nightly check. All data is
synthetic; core's CLI and the drafter are faked."""
import http.client
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "portal"
sys.path.insert(0, str(PLUGIN))

from gnomonkit_portal import cli, refresh, server, snapshot, token, updates  # noqa: E402

HID = "examplemkt-0123456789ab"
PIN, HEAD = "a" * 40, "b" * 40
SECRET_HOST = "git." + "internal-" + "host.example"
CRED = "ghp_" + "x" * 8 + "fake"
URL = "https://user:%s@%s/example-org/plugins.git" % (CRED, SECRET_HOST)


def gate_files(home: Path, overlay=True, signers=False):
    sync = home / "sync"
    (sync / "held").mkdir(parents=True)
    last = {"state": "held", "held": HID, "pin": PIN, "head": HEAD, "checked": "2026-01-02T03:04:05Z",
            "summary": "1 commit unsigned", "error": "fetch from %s failed" % URL}
    if signers:
        last["suggested_signers"] = 1
    (sync / "sources.json").write_text(json.dumps({
        "version": 1, "checked": 1700000000, "overlay": overlay,
        "sources": {"examplemkt": {"url": URL, "branch": "main", "last": last},
                    "broken": {"url": URL, "branch": "main",
                               "last": {"state": "error", "error": "cannot reach " + SECRET_HOST}}}}), encoding="utf-8")
    (sync / "held" / (HID + ".json")).write_text(json.dumps({
        "id": HID, "source": "examplemkt", "pin": PIN, "head": HEAD, "branch": "main", "url": URL, "commits": 2,
        "created": "2026-01-02T03:04:05Z", "signers": "overlay", "contents": "print('file body')",
        "reasons": [{"kind": "unsigned", "commit": HEAD, "text": "commit bbbbbbb is not signed"},
                    {"kind": "new-contributor", "who": "Example Person", "text": "a new contributor changed code",
                     "paths": ["plugins/x/hooks/a.py"], "commits": [HEAD], "email": "someone@" + SECRET_HOST}],
        "files": [{"path": "plugins/x/hooks/a.py", "added": 3, "deleted": 1, "patch": "+secret line"}],
        "files_total": 1}), encoding="utf-8")
    # a held file whose id does not match its name is ignored
    (sync / "held" / "other-0123456789ab.json").write_text(json.dumps({"id": HID}), encoding="utf-8")


class Live:
    def __init__(self, tmp, **cfg_kw):
        self.drafts = []
        kw = {"refresher": lambda: {"ok": True}, "searcher": lambda q, c: {"results": []}, "mem_cli": "",
              "drafter": cfg_kw.pop("drafter", self._fake_draft)}
        cfg = server.Config(port=0, data_dir=tmp / "portal", **cfg_kw)
        self.srv = server.make_server(cfg, **kw)
        self.port = self.srv.server_address[1]
        self.origin = {"Origin": "http://127.0.0.1:%d" % self.port, "Host": "127.0.0.1:%d" % self.port}
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def _fake_draft(self, hid):
        self.drafts.append(hid)
        return 200, {"ok": True, "id": hid, "branch": "gnomonkit-sync/" + hid, "already": False,
                     "pr": "https://github.com/example-org/overlay/pull/7", "compare": None}

    def req(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def approve(self, hid=HID, headers=None, ctype="application/json"):
        h = dict(self.origin, **({"Content-Type": ctype} if ctype else {}))
        h.update(headers or {})
        return self.req("POST", "/api/sync/approve", h, json.dumps({"id": hid}))

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test gets its own GNOMONKIT_HOME and portal folder, so nothing reads or writes the real ones."""
    h = tmp_path / "gk"
    monkeypatch.setenv("GNOMONKIT_HOME", str(h))
    monkeypatch.setenv("GNOMONKIT_PORTAL_DIR", str(tmp_path / "portal"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("GNOMONKIT_CLI", raising=False)
    return h


def test_approval_allowed_only_in_plain_loopback():
    assert server.approval_allowed("loopback") is True
    for mode in ("token", "proxy", "snapshot", "LOOPBACK", "loopback ", "", None, 0):
        assert server.approval_allowed(mode) is False, mode


def test_sync_view_is_whitelisted(home, tmp_path):
    gate_files(home)
    lv = Live(tmp_path)
    try:
        st, h, body = lv.req("GET", "/api/sync")
        assert st == 200 and h["Content-Type"].startswith("application/json")
        text = body.decode("utf-8")
        for bad in (SECRET_HOST, CRED, "https://", "file body", "secret line", "fetch from", "cannot reach"):
            assert bad not in text, bad
        v = json.loads(body)
        assert v["approval"] is True and v["overlay"] is True
        assert [s["name"] for s in v["sources"]] == ["broken", "examplemkt"]
        ex = v["sources"][1]
        assert ex["state"] == "held" and ex["held"] == HID and ex["summary"] == "1 commit unsigned"
        assert "url" not in ex and "error" not in ex
        assert [x["id"] for x in v["held"]] == [HID]
        held = v["held"][0]
        assert held["commits"] == 2 and held["files"] == [{"path": "plugins/x/hooks/a.py", "added": 3, "deleted": 1}]
        assert held["reasons"][1] == {"kind": "new-contributor", "text": "a new contributor changed code",
                                      "commit": None, "who": "Example Person", "paths": ["plugins/x/hooks/a.py"]}
        assert "url" not in held and "signers" not in held
        assert json.loads(lv.req("GET", "/api/status")[2])["sync_approval"] is True
    finally:
        lv.close()


def test_reason_kinds_core_writes_are_kept_and_others_are_other():
    assert updates._reason({"kind": "overlay-unavailable", "text": "x"})["kind"] == "overlay-unavailable"
    assert updates._reason({"kind": "made-up", "text": "x"})["kind"] == "other"


def test_sync_view_with_nothing_adopted(home, tmp_path):
    v = updates.public_view()
    assert v == {"sources": [], "held": [], "overlay": None, "checked_at": None}
    (home / "sync").mkdir(parents=True)
    (home / "sync" / "sources.json").write_text("{not json", encoding="utf-8")
    assert updates.public_view()["sources"] == []


def test_draft_approval_in_loopback(home, tmp_path):
    gate_files(home)
    lv = Live(tmp_path)
    try:
        assert lv.req("POST", "/api/sync/approve", {"Content-Type": "application/json"}, '{"id":"%s"}' % HID)[0] == 403
        assert lv.approve(headers={"Origin": "http://evil.example"})[0] == 403
        assert lv.approve(headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
        assert lv.approve(headers={"Host": "attacker.example", "Origin": "http://attacker.example"})[0] == 421
        assert lv.approve(ctype="text/plain")[0] == 415
        assert lv.approve(ctype=None)[0] == 415
        assert lv.approve(hid="../../etc")[0] == 400
        assert lv.approve(hid="examplemkt-ffffffffffff")[0] == 404           # well-formed, but not held
        big = dict(lv.origin, **{"Content-Type": "application/json"})
        assert lv.req("POST", "/api/sync/approve", big, json.dumps({"id": HID, "pad": "x" * 2000}))[0] == 413
        assert lv.req("POST", "/api/sync/approve", big, "{nope")[0] == 400
        assert lv.drafts == []
        st, _, body = lv.approve()
        r = json.loads(body)
        assert st == 200 and r["branch"] == "gnomonkit-sync/" + HID and lv.drafts == [HID]
        st, _, _ = lv.approve(headers={"Origin": "", "Sec-Fetch-Site": "same-origin"})
        assert st == 200 and lv.drafts == [HID, HID]
    finally:
        lv.close()


def test_release_key_draft_is_offered(home, tmp_path):
    gate_files(home, signers=True)
    lv = Live(tmp_path)
    try:
        v = json.loads(lv.req("GET", "/api/sync")[2])
        assert v["sources"][1]["signers_draft"] == "examplemkt-signers"
        assert lv.approve(hid="examplemkt-signers")[0] == 200 and lv.drafts == ["examplemkt-signers"]
        assert lv.approve(hid="broken-signers")[0] == 404
    finally:
        lv.close()


@pytest.mark.parametrize("mode", ["token", "proxy"])
def test_no_draft_endpoint_outside_loopback(home, tmp_path, mode):
    gate_files(home)
    tok = token.generate() if mode == "token" else None
    proxy = mode == "proxy"
    # Proxy mode needs an allowed host, and the proxy passes that host on with each request.
    lv = Live(tmp_path, token=tok, behind_proxy=proxy, allowed_hosts={"portal.example.com"} if proxy else set())
    try:
        auth = ({server.TOKEN_HEADER: tok} if tok
                else {"Host": "portal.example.com", "Origin": "https://portal.example.com"})
        assert lv.approve(headers=auth)[0] == 404
        assert lv.approve(headers=dict(auth, **{"Sec-Fetch-Site": "same-origin"}))[0] == 404
        assert lv.drafts == []
        st, _, body = lv.req("GET", "/api/sync", auth)
        assert st == 200 and json.loads(body)["approval"] is False
        assert json.loads(lv.req("GET", "/api/status", auth)[2])["sync_approval"] is False
        if tok:
            assert lv.req("GET", "/api/sync")[0] == 401
    finally:
        lv.close()


def test_one_draft_at_a_time(home, tmp_path):
    gate_files(home)
    gate, entered = threading.Event(), threading.Event()

    def slow(hid):
        entered.set()
        gate.wait(5)
        return 200, {"ok": True, "id": hid, "branch": "b", "already": False, "pr": None, "compare": None}

    lv = Live(tmp_path, drafter=slow)
    try:
        first = []
        t = threading.Thread(target=lambda: first.append(lv.approve()[0]))
        t.start()
        assert entered.wait(5)
        assert lv.approve()[0] == 409
        gate.set()
        t.join(5)
        assert first == [200]
    finally:
        gate.set()
        lv.close()


def test_a_failing_drafter_is_a_500_not_a_crash(home, tmp_path):
    gate_files(home)

    def boom(hid):
        raise RuntimeError("no")

    lv = Live(tmp_path, drafter=boom)
    try:
        st, _, body = lv.approve()
        assert st == 500 and "RuntimeError" in json.loads(body)["error"]
        assert lv.req("GET", "/api/sync")[0] == 200
    finally:
        lv.close()


# ------------------------------------------------------------------ the call into core
def fake_cli(tmp_path, monkeypatch):
    f = tmp_path / "gnomonkit.py"
    f.write_text("# fake\n", encoding="utf-8")
    monkeypatch.setenv("GNOMONKIT_CLI", str(f))
    return f


def test_draft_runs_core_approve_draft(tmp_path, monkeypatch):
    f = fake_cli(tmp_path, monkeypatch)
    calls = []

    def ok(argv, **kw):
        calls.append((argv, kw))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"branch": "gnomonkit-sync/" + HID, "already": True,
                                                               "pr": None, "compare": None, "commit": "c" * 40}), stderr="")

    code, body = updates.draft(HID, runner=ok)
    assert code == 200 and body["already"] is True and "commit" not in body
    argv, kw = calls[0]
    assert argv[1:] == [str(f), "sync", "approve-draft", HID, "--json"]
    assert kw["stdin"] is not None and kw["timeout"] > 0 and "creationflags" in kw

    def fails(argv, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="gnomonkit: no overlay is set\n")

    assert updates.draft(HID, runner=fails) == (502, {"error": "no overlay is set"})
    assert updates.draft("--help", runner=ok)[0] == 400 and len(calls) == 1


def test_draft_without_core(tmp_path, monkeypatch):
    monkeypatch.setenv("GNOMONKIT_CLI", str(tmp_path / "missing.py"))
    assert updates.find_core_cli() is None
    assert updates.draft(HID)[0] == 503
    assert updates.check_due() is None


def test_core_cli_found_beside_the_portal(monkeypatch):
    monkeypatch.delenv("GNOMONKIT_CLI", raising=False)
    found = updates.find_core_cli()
    assert found is not None and found.name == "gnomonkit.py" and found.parent.name == "core"


def test_nightly_refresh_runs_the_sync_check(tmp_path, monkeypatch):
    monkeypatch.setenv("GNOMONKIT_PORTAL_DIR", str(tmp_path / "portal"))
    monkeypatch.setattr(refresh, "run", lambda trigger, **kw: {"ok": True})
    ran = []
    monkeypatch.setattr(updates, "check_due", lambda: ran.append(1) or True)
    handlers = list(server.log.handlers)            # the refresh command adds a file handler; leave none behind
    try:
        assert cli.main(["refresh", "--trigger", "nightly"]) == 0
        assert ran == [1]
        assert cli.main(["refresh", "--trigger", "cli"]) == 0
        assert ran == [1]
    finally:
        for h in [h for h in server.log.handlers if h not in handlers]:
            server.log.removeHandler(h)
            h.close()


def test_check_due_argv(tmp_path, monkeypatch):
    f = fake_cli(tmp_path, monkeypatch)
    seen = []
    assert updates.check_due(runner=lambda argv, **kw: seen.append(argv) or SimpleNamespace(returncode=0)) is True
    assert seen[0][1:] == [str(f), "sync", "check", "--if-due", "--quiet"]
    assert updates.check_due(runner=lambda argv, **kw: SimpleNamespace(returncode=1)) is False


def test_page_has_the_updates_tab():
    page = (PLUGIN / "gnomonkit_portal" / "page.html").read_text(encoding="utf-8")
    for needle in ('id="t-updates"', 'id="p-updates"', '"updates"', "/api/sync/approve", "/api/sync\"",
                   "Draft approval", "v.approval"):
        assert needle in page, needle
    # the button is drawn only when the server says approval is allowed
    assert "if (v.approval) d.append(draftButton" in page


def test_a_snapshot_has_no_updates_tab(tmp_path):
    # A static snapshot has no server to ask for /api/sync, so the tab is live-only.
    html = snapshot.build(tmp_path / "none")
    for gone in ('id="t-updates"', 'id="p-updates"'):
        assert gone not in html, gone
    assert 'id="t-effectiveness"' in html
