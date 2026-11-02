"""Portal plugin: the whitelisted view, routing and access control, escaping, refresh, memory search and the
installer's plans for every OS. All data is synthetic; the OS calls are mocked."""
import base64
import hashlib
import http.client
import json
import os
import re
import socket
import stat
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "portal"
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(PLUGIN / "scripts"))

from gnomonkit_portal import cli, memory, paths, refresh, server, snapshot, token, trust, view  # noqa: E402
import install_portal as IP  # noqa: E402

XSS = "<script>alert(1)</script> & <img src=x onerror=alert(2)>"
SECRET_DESC = "describe-" + "the-" + "private-task"        # a spawn description: must never be served
CWD = "/work/" + "demo-app"
PKEY = "-work-" + "demo-app"                              # a transcript folder name that encodes the path


def metrics(**over):
    m = {
        "schema": 1, "version": "0.0.0-test", "generated_at": "2026-01-02T03:04:05+00:00", "since_days": 30,
        "window_from": "2025-12-03T03:04:05",
        "sources": {"sessions": 3, "transcripts": 5, "cache_hits": 2, "scanned": 3, "session_spend_total": 999},
        "headline": {"tokens_saved": {"value": 120000, "label": "ESTIMATED", "parts": {"subagents": 100000, "digest": 20000}},
                     "subagent_spend": {"value": 50000, "label": "MEASURED"}, "spawns": {"value": 4, "label": "MEASURED"},
                     "worth_it": {"value": 3, "of": 4, "label": "ESTIMATED"}},
        "subagents": {"spawns": 4, "linked": 4, "worth_it": 3, "not_worth_it": 1, "small": 1, "reads_avoided": 100000,
                      "spend": {"input": 1, "cache_creation": 2, "cache_read": 3, "output": 4, "total": 50000},
                      "labels": {"spend": "MEASURED"},
                      "by_type": {"Explore": {"spawns": 3, "linked": 3, "worth_it": 3, "spend": 30000, "worth_it_share": 1.0}},
                      "by_project": {PKEY: {"spawns": 4, "spend": 50000}},
                      "by_week": {"2026-W01": {"spawns": 2, "linked_spend": 20000, "days_in_window": 3.5, "partial": True},
                                  "2026-W02": {"spawns": 2, "linked_spend": 30000, "days_in_window": 7.0, "partial": False}},
                      "not_worth_it_spawns": [{"agent": "a1", "type": "general-purpose", "project": PKEY, "week": "2026-W01",
                                               "desc": SECRET_DESC, "spend": {"total": 9000}, "reads_avoided": 10,
                                               "returned": 5, "kept_out": 4}],
                      "spawn_rows": [{"desc": SECRET_DESC}]},
        "main_context": {"budget": 250000, "sessions": 3, "turns": 40, "reread": 900000, "reread_capped": 800000,
                         "peak": 300000, "median": 90000, "compacted_sessions": 1, "compactions": 1,
                         "over": {"200000": {"turns_share": 0.1, "reread_share": 0.3}},
                         "labels": {"turns": "MEASURED"},
                         "per_session": [{"session": "sess-0001-aaaa", "project": "demo-app", "turns": 20, "peak": 300000,
                                          "reread": 600000, "compactions": 1}]},
        "compaction": {PKEY: {"cwd": CWD, "sessions": 3, "turns": 40, "prompts": 12, "base": 20000, "growth_median": 3000,
                              "growth_p90": 9000, "peaks": [1, 2],
                              "compaction": {"count": 1, "auto": 1, "manual": 0, "reread_pre": 0.1, "reread_post": 0.2,
                                             "corrected": 0, "correction_checked": 1, "events": [{"ts": "x"}]}}},
        "budgets": {"schema": 1, "default_budget": 250000, "label": "ESTIMATED", "projects": [
            {"project": PKEY, "cwd": CWD, "current": 250000, "recommended": 300000, "change": 50000,
             "confidence": "medium", "rule": "growth", "reason": "base + p90 growth"}]},
        "hooks": {"router": {"diff-review": {"suggested": 4, "taken": 2, "uptake": 0.5}},
                  "interlock": {"blocks": 2, "outcomes": {"rewritten": 2}}, "correction_watch": {"fires": 1},
                  "freshness": {"notices": 0}, "palace": {"sessions": 1, "injected": 4, "cited": 1, "cite_rate": 0.25}},
        "digest": {"command": "my-wrapper", "calls": 0},
        "readiness": {"label": "per row", "summary": {"ok": 1, "fail": 1}, "worst": "fail", "rows": [
            {"id": "skill_uptake", "metric": "Skill uptake", "value": 0.6, "display": "60%", "n": 12, "target": ">= 50%",
             "status": "ok", "label": "MEASURED", "definition": "share of sessions", "note": "", "detail": {"sessions": 12}},
            {"id": "hook_errors", "metric": "Hook errors", "value": 0.2, "display": "20%", "n": 30, "target": "<= 1%",
             "status": "fail", "label": "MEASURED", "definition": "share of runs", "note": "",
             "detail": {"runs": 30, "samples": [SECRET_DESC]}}]},
        "usage": usage_section(),
        "interjector": {"note": "x"}, "state": {}, "method": {},
    }
    m.update(over)
    return m


SUB_SRC = "/work/" + "home-of-someone/.gnomonkit/subscription.json"     # the real path the collector reports


def usage_section(status="ok"):
    """The collector's usage section in its real shape (core 0.17.0), with synthetic numbers."""
    planned = status == "ok"
    sched = [{"from": "2025-12", "plan": "Example plan", "monthly_usd": 31.0}] if planned else []
    p = (lambda v: v) if planned else (lambda v: None)
    return {
        "label": "API-equivalent estimate", "note": "not billed per token",
        "prices_usd_per_mtok": {"claude-test-model": {"input": 1.0}},
        "subscription": {"status": status, "source": SUB_SRC, "schedule": sched,
                         "note": "" if planned else "add a schedule to %s" % SUB_SRC,
                         "method": "plan dollars per calendar day = monthly_usd / days in that month"},
        "total": {"turns": 40, "input": 1, "output": 2, "cache_creation": 3, "cache_read": 4, "total": 1000000,
                  "est_cost_usd": 124.0, "unpriced_turns": 0, "unpriced_tokens": 0,
                  "subscription_usd": p(31.0), "subscription_usd_allocated": p(31.0),
                  "api_equiv_usd_planned": p(124.0), "value_multiple": p(4.0)},
        "months": [{"month": "2026-01", "plan": "Example plan" if planned else None, "subscription_usd": p(31.0),
                    "subscription_usd_window": p(31.0), "api_equiv_usd": 124.0, "window_days": 31.0,
                    "days_in_month": 31, "covers_full_month": True, "value_multiple": p(4.0)}],
        "month_to_date": {"month": "2026-01", "plan": "Example plan" if planned else None, "subscription_usd": p(31.0),
                          "api_equiv_usd": 62.0, "days_elapsed": 10.5, "days_in_month": 31,
                          "projected_api_equiv_usd": 183.05, "projected_value_multiple": p(5.9),
                          "plan_value_days_used": p(62.0), "complete": True, "label": "ESTIMATED (linear run rate)"},
        "daily_timezone": "UTC",
        "daily": [{"date": "2026-01-01", "turns": 20, "total": 500000, "est_cost_usd": 62.0,
                   "unpriced_turns": 0, "unpriced_tokens": 0, "subscription_usd_share": p(1.0)}],
        "projects": [{"project": PKEY, "label": "demo-app", "sessions": 3, "turns": 40, "total": 1000000,
                      "est_cost_usd": 124.0, "subscription_usd_share": p(31.0)}],
        "sessions": [{"session": "sess-0001-aaaa", "project": PKEY, "label": "demo-app", "title": XSS,
                      "first_ts": "2026-01-01T00:00:00Z", "last_ts": "2026-01-01T01:00:00Z", "total": 600000,
                      "est_cost_usd": 74.4, "subscription_usd_share": p(18.6)}],
        "session_count": 1,
        "models": {"claude-test-model": {"turns": 40, "total": 1000000, "est_cost_usd": 124.0}},
    }


# ------------------------------------------------------------------ view
def test_view_keeps_numbers_and_drops_private_fields():
    v = view.public_view(metrics())
    blob = json.dumps(v)
    assert SECRET_DESC not in blob                     # spawn descriptions and samples
    assert CWD not in blob and PKEY not in blob        # paths and path-encoding folder names
    assert "my-wrapper" not in blob                    # the user's digest wrapper name
    s = v["sections"]
    assert s["effectiveness"]["headline"]["tokens_saved"]["value"] == 120000
    assert s["effectiveness"]["subagents"]["by_project"][0]["name"] == "demo-app"
    assert s["effectiveness"]["budgets"]["projects"][0]["project"] == "demo-app"
    assert s["effectiveness"]["subagents"]["not_worth_it"][0]["spend"] == 9000
    assert s["readiness"]["rows"][1]["detail"] == {"runs": 30}
    assert s["usage"]["sessions"][0]["session"] == "sess-000"
    assert s["effectiveness"]["main_context"]["top_sessions"][0]["title"] == XSS[:view.TITLE_MAX]


def test_usage_subscription_real_shape():
    u = view.public_view(metrics())["sections"]["usage"]
    assert u["subscription"]["status"] == "ok"
    assert u["subscription"]["schedule"] == [{"from": "2025-12", "plan": "Example plan", "monthly_usd": 31.0}]
    assert "note" not in u["subscription"] and "source" not in u["subscription"]
    mtd = u["month_to_date"]
    assert (mtd["subscription_usd"], mtd["api_equiv_usd"], mtd["projected_api_equiv_usd"], mtd["projected_value_multiple"]) \
        == (31.0, 62.0, 183.05, 5.9)
    assert mtd["plan"] == "Example plan" and mtd["complete"] is True and mtd["plan_value_days_used"] == 62.0
    assert u["months"][0]["value_multiple"] == 4.0 and u["months"][0]["covers_full_month"] is True
    assert u["total"]["subscription_usd"] == 31.0 and u["total"]["value_multiple"] == 4.0
    assert u["projects"][0]["subscription_usd_share"] == 31.0
    assert u["sessions"][0]["subscription_usd_share"] == 18.6
    assert u["daily"][0]["subscription_usd_share"] == 1.0


def test_missing_subscription_never_leaks_the_schedule_path():
    m = metrics(usage=usage_section("missing"))
    u = view.public_view(m)["sections"]["usage"]
    assert u["subscription"]["status"] == "missing" and u["subscription"]["schedule"] == []
    assert "No subscription schedule" in u["subscription"]["note"]
    assert "home-of-someone" not in json.dumps(view.public_view(m))
    assert "subscription_usd_share" not in u["projects"][0]                 # null shares are dropped
    assert "home-of-someone" not in json.dumps(view.public_view(metrics()))
    assert view.public_view(metrics(usage=usage_section("weird")))["sections"]["usage"]["subscription"]["status"] == "missing"


def test_router_rows_name_the_suggesting_plugin():
    m = metrics()
    m["hooks"]["router"] = {"diff-review": {"suggested": 4, "taken": 2, "uptake": 0.5, "by": {"core": 3, "overlay": 1},
                                            "follow_through": 0.25},
                            "local-only": {"suggested": 1, "taken": 0, "by": {"unknown": 1}},
                            "odd": {"suggested": 1, "by": {"x": "not a number", "y": 1}}}
    rows = {r["name"]: r for r in view.public_view(m)["sections"]["effectiveness"]["hooks"]["router"]}
    assert set(rows) == {"core:diff-review", "local-only", "y:odd"}
    assert rows["core:diff-review"]["follow_through"] == 0.25 and "by" not in rows["core:diff-review"]
    assert "r.follow_through" in (PLUGIN / "gnomonkit_portal" / "page.html").read_text(encoding="utf-8")


def test_part_weeks_and_the_daily_timezone_reach_the_page():
    v = view.public_view(metrics())["sections"]
    w1, w2 = v["effectiveness"]["subagents"]["by_week"]
    assert (w1["name"], w1["partial"], w1["days_in_window"], w1["linked_spend"]) == ("2026-W01", True, 3.5, 20000)
    assert (w2["partial"], w2["days_in_window"]) == (False, 7.0)
    assert v["usage"]["daily_timezone"] == "UTC"
    m = metrics()                                      # only a real true marks a part week
    m["subagents"]["by_week"]["2026-W02"]["partial"] = "yes"
    assert view.public_view(m)["sections"]["effectiveness"]["subagents"]["by_week"][1]["partial"] is False
    page = (PLUGIN / "gnomonkit_portal" / "page.html").read_text(encoding="utf-8")
    for f in ("r.partial", "part week", "days_in_window", "sec.daily_timezone", "Spawns per week (UTC)"):
        assert f in page, f


def test_page_uses_the_real_subscription_fields():
    page = (PLUGIN / "gnomonkit_portal" / "page.html").read_text(encoding="utf-8")
    for f in ("month_to_date", "projected_api_equiv_usd", "projected_value_multiple", "subscription_usd_share",
              "subscription_usd_window", "Share of plan $", "API-equivalent $"):
        assert f in page, f
    assert "subKeys" not in page and "fmtKey" not in page


def test_hide_titles_removes_every_title():
    v = view.public_view(metrics(), hide_titles=True)
    assert "alert" not in json.dumps(v)
    assert v["meta"]["hide_titles"] is True


def test_absent_sections_are_absent_and_garbage_is_safe():
    m = metrics()
    for k in ("readiness", "usage"):
        m.pop(k)
    v = view.public_view(m)
    assert "readiness" not in v["sections"] and "usage" not in v["sections"]
    assert view.public_view({})["sections"] == {}
    assert view.public_view(["not", "a", "dict"])["sections"] == {}
    weird = view.public_view({"usage": {"sessions": "x", "daily": [1, None], "models": 3}, "readiness": {"rows": [1]}})
    assert weird["sections"]["readiness"]["rows"] == []


def test_dumps_escapes_markup():
    raw = view.dumps(view.public_view(metrics())).decode("utf-8")
    assert "<" not in raw and ">" not in raw and "&" not in raw
    assert json.loads(raw)["sections"]["usage"]["sessions"][0]["title"] == XSS


def test_page_never_writes_markup_from_strings():
    page = (PLUGIN / "gnomonkit_portal" / "page.html").read_text(encoding="utf-8")
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert bad not in page, bad
    assert page.count("__NONCE__") == 2


# ------------------------------------------------------------------ server
class Live:
    def __init__(self, tmp, **cfg_kw):
        self.d = tmp
        (tmp / "metrics.json").write_text(json.dumps(metrics()), encoding="utf-8")
        self.refreshes = []
        kw = {"refresher": self._fake_refresh, "searcher": lambda q, cli: {"query": q, "results": [], "error": None},
              "mem_cli": cfg_kw.pop("mem_cli", ""), "mem_index": cfg_kw.pop("mem_index", ""),
              "trust_reader": cfg_kw.pop("trust_reader", lambda: None)}       # never the real core or home
        cfg_kw.setdefault("work_file", tmp / "work-items.json")      # never the real home's summary
        cfg = server.Config(port=0, data_dir=tmp, **cfg_kw)
        self.srv = server.make_server(cfg, **kw)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def _fake_refresh(self):
        self.refreshes.append(time.time())
        return {"ok": True}

    def req(self, method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, dict(r.getheaders()), data

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def live(tmp_path):
    lv = Live(tmp_path)
    yield lv
    lv.close()


def test_routes_and_headers(live):
    st, h, body = live.req("GET", "/")
    assert st == 200 and b"Gnomonkit Portal" in body and b"__NONCE__" not in body
    assert "nonce-" in h["Content-Security-Policy"] and "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff" and h["X-Frame-Options"] == "DENY"
    st, h, body = live.req("GET", "/api/metrics")
    assert st == 200 and h["Content-Type"].startswith("application/json")
    assert json.loads(body)["sections"]["usage"]["total"]["est_cost_usd"] == 124.0
    assert b"<" not in body and SECRET_DESC.encode() not in body
    st, _, body = live.req("GET", "/api/status")
    s = json.loads(body)
    assert st == 200 and s["auth"] == "loopback" and s["memory"] is False and s["has_data"] is True
    for p in ("/metrics.json", "/api/raw", "/../metrics.json", "/api/memory?q=x"):
        assert live.req("GET", p)[0] == 404, p
    assert live.req("PUT", "/api/metrics")[0] == 405
    assert live.req("DELETE", "/")[0] == 405
    assert live.req("GET", "/healthz")[0] == 200


def test_cross_site_and_rebinding_refused(live):
    assert live.req("GET", "/api/metrics", {"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert live.req("GET", "/api/metrics", {"Host": "attacker.example"})[0] == 421
    assert live.req("GET", "/api/metrics", {"Host": "localhost:1234"})[0] == 200


def test_refresh_needs_same_origin_and_is_rate_limited(live):
    assert live.req("POST", "/api/refresh")[0] == 403                                    # no Origin: CSRF-shaped
    assert live.req("POST", "/api/refresh", {"Origin": "http://evil.example"})[0] == 403
    origin = {"Origin": "http://127.0.0.1:%d" % live.port, "Host": "127.0.0.1:%d" % live.port}
    st, _, body = live.req("POST", "/api/refresh", origin)
    assert st == 202 and json.loads(body)["started"] is True
    st, h, body = live.req("POST", "/api/refresh", origin)
    assert st == 429 and int(h["Retry-After"]) > 0
    assert live.req("POST", "/api/other", origin)[0] == 404
    time.sleep(0.2)
    assert len(live.refreshes) == 1


def test_metrics_missing_gives_empty_view(tmp_path):
    lv = Live(tmp_path)
    try:
        (tmp_path / "metrics.json").unlink()
        st, _, body = lv.req("GET", "/api/metrics")
        v = json.loads(body)
        assert st == 200 and v["sections"] == {} and v["empty"] is True
    finally:
        lv.close()


def test_token_mode(tmp_path):
    tok = token.generate()
    lv = Live(tmp_path, token=tok)
    try:
        assert lv.req("GET", "/api/metrics")[0] == 401
        st, _, body = lv.req("GET", "/")
        assert st == 401 and b"Sign in needed" in body and b"sections" not in body
        assert lv.req("GET", "/api/metrics", {server.TOKEN_HEADER: tok})[0] == 200
        assert lv.req("GET", "/api/metrics", {"Authorization": "Bearer " + tok})[0] == 200
        assert lv.req("GET", "/api/metrics", {server.TOKEN_HEADER: tok[:-1] + "x"})[0] == 401
        assert lv.req("GET", "/?token=wrong")[0] == 401
        st, h, _ = lv.req("GET", "/?token=" + tok)
        assert st == 303 and h["Location"] == "/"
        ck = h["Set-Cookie"]
        assert "HttpOnly" in ck and "SameSite=Strict" in ck and "Secure" not in ck
        cookie = {"Cookie": ck.split(";")[0]}
        assert lv.req("GET", "/api/metrics", cookie)[0] == 200
        # Any Host passes in token mode (a rebinding page has no token), but the cookie alone cannot POST cross-origin.
        assert lv.req("POST", "/api/refresh", cookie)[0] == 403
        assert lv.req("POST", "/api/refresh", {server.TOKEN_HEADER: tok})[0] == 202
        st, h, _ = lv.req("GET", "/?token=" + tok, {"X-Forwarded-Proto": "https"})
        assert "Secure" in h["Set-Cookie"]
    finally:
        lv.close()


def test_access_log_never_records_the_token(tmp_path):
    tok = token.generate()
    server.setup_logging(tmp_path, to_stderr=False)
    lv = Live(tmp_path, token=tok)
    try:
        lv.req("GET", "/?token=" + tok)
        for h in server.log.handlers:
            h.flush()
        assert tok not in (tmp_path / "portal.log").read_text(encoding="utf-8")
    finally:
        lv.close()


def test_memory_tab_only_when_enabled(tmp_path):
    lv = Live(tmp_path, mem_cli="mempalace-fake", memory="auto")
    try:
        assert json.loads(lv.req("GET", "/api/status")[2])["memory"] is True
        st, _, body = lv.req("GET", "/api/memory?q=hello")
        assert st == 200 and json.loads(body)["query"] == "hello"
    finally:
        lv.close()


WORK_REPO = "demo-" + "owner/demo-" + "work"
WORK_BODY = "body-" + "text-that-" + "stays-local"


def work_summary(now=1_800_000_000):
    iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))      # noqa: E731
    return {"version": 1, "repos": {
        WORK_REPO: {"read_at": iso(now), "stale_hours": 24, "items": [
            {"number": 3, "title": XSS, "state": "active", "url": "javascript:alert(1)", "body": WORK_BODY,
             "created": iso(now - 5 * 86400), "updated": iso(now - 3600), "last_activity": now - 3600,
             "claimed_by": "demo:session-a", "machine": "lab-box", "stale": False},
            {"number": 4, "title": "Old claim", "state": "active", "created": iso(now - 9 * 86400),
             "last_activity": now - 30 * 3600, "claimed_by": "demo:session-b", "stale": False},
            {"number": 5, "title": "Waiting", "state": "blocked", "created": iso(now - 86400), "updated": iso(now - 600)},
            {"number": 6, "title": "Closed one", "state": "closed"},
            {"number": "7", "title": "Bad number", "state": "open"},
            {"number": 8, "title": "Unclaimed", "state": "open", "comments": [{"body": WORK_BODY}]},
        ]},
        "../escape": {"items": [{"number": 1, "title": "x", "state": "open"}]},
        "not a repo": {"items": [{"number": 1, "title": "x", "state": "open"}]},
    }}


def test_work_view_shows_states_and_links_but_never_bodies():
    now = 1_800_000_000
    v = view.work_view(work_summary(now), now=now)
    assert [(r["number"], r["state"]) for r in v["items"]] == [(3, "active"), (4, "active"), (5, "blocked"), (8, "open")]
    assert all(r["repo"] == WORK_REPO for r in v["items"]) and [r["repo"] for r in v["repos"]] == [WORK_REPO]
    first, old = v["items"][0], v["items"][1]
    assert first["url"] == "https://github.com/%s/issues/3" % WORK_REPO          # rebuilt, never the file's URL
    assert first["idle_s"] == 3600 and first["age_s"] == 5 * 86400 and first["stale"] is False
    assert old["stale"] is True                                                   # 30 h idle against 24 h
    assert first["title"] == XSS and first["claimed_by"] == "demo:session-a" and first["machine"] == "lab-box"
    assert v["counts"] == {"active": 2, "blocked": 1, "review": 0, "open": 1, "stale": 1}
    raw = view.dumps(v).decode("utf-8")
    assert WORK_BODY not in raw and "javascript" not in raw and "<" not in raw
    hidden = view.work_view(work_summary(now), hide_titles=True, now=now)
    for r in hidden["items"]:
        assert not {"title", "claimed_by", "machine"} & set(r), r
    assert "Old claim" not in view.dumps(hidden).decode("utf-8") and hidden["hide_titles"] is True
    for junk in (None, [], {"repos": []}, {"repos": {WORK_REPO: {"items": "x"}}}):
        assert view.work_view(junk)["items"] == []


def test_work_route(tmp_path):
    lv = Live(tmp_path)
    try:
        st, h, body = lv.req("GET", "/api/work")
        v = json.loads(body)
        assert st == 200 and v["items"] == [] and v["empty"] is True and h["Content-Type"].startswith("application/json")
        (tmp_path / "work-items.json").write_text(json.dumps(work_summary(int(time.time()))), encoding="utf-8")
        st, _, body = lv.req("GET", "/api/work")
        v = json.loads(body)
        assert st == 200 and len(v["items"]) == 4 and "empty" not in v
        assert WORK_BODY.encode() not in body and b"<" not in body
        page = lv.req("GET", "/")[2]
        assert b'id="t-work"' in page and b'id="p-work"' in page and b"/api/work" in page
    finally:
        lv.close()
    tok = token.generate()
    lv = Live(tmp_path, token=tok, hide_titles=True)
    try:
        assert lv.req("GET", "/api/work")[0] == 401
        st, _, body = lv.req("GET", "/api/work", {server.TOKEN_HEADER: tok})
        assert st == 200 and b"Old claim" not in body and b"session-a" not in body
    finally:
        lv.close()


def test_a_snapshot_has_no_work_tab(tmp_path):
    # The work summary is live-only: a static snapshot has neither the tab nor any link to an item.
    html = snapshot.build(tmp_path / "none")
    for gone in ('id="t-work"', 'id="p-work"'):
        assert gone not in html, gone
    assert "://" not in html and 'id="t-effectiveness"' in html


def test_second_server_cannot_take_the_port(live):
    """http.server's SO_REUSEADDR lets a second Windows process bind a taken port; the portal's bind is exclusive."""
    cfg = server.Config(port=live.port, data_dir=live.d)
    with pytest.raises(OSError):
        server.make_server(cfg, mem_cli="")
    if os.name == "nt":
        assert server.PortalHTTPServer.allow_reuse_address is False


def test_check_start_refuses_open_binds():
    assert server.check_start("127.0.0.1", None, False) == "loopback"
    assert server.check_start("::1", None, False) == "loopback"
    assert server.check_start("localhost", None, False) == "loopback"
    for host in ("0.0.0.0", "192.0.2.10", "::"):
        with pytest.raises(server.StartRefused):
            server.check_start(host, None, False)
        assert server.check_start(host, "t" * 40, False) == "token"
        assert server.check_start(host, None, True, {"portal.example.com"}) == "proxy"
    with pytest.raises(server.StartRefused):
        server.check_start("portal.example.com", "t" * 40, False)


def test_behind_proxy_needs_allowed_host(tmp_path, monkeypatch, capsys):
    """Without a Host check a DNS-rebinding page reaches the API through the proxy."""
    for hosts in ((), {""}, {"  "}):
        with pytest.raises(server.StartRefused, match="--allowed-host"):
            server.check_start("127.0.0.1", None, True, hosts)
    from gnomonkit_portal import cli
    monkeypatch.setenv("GNOMONKIT_PORTAL_DIR", str(tmp_path))
    monkeypatch.delenv("GNOMONKIT_PORTAL_TOKEN", raising=False)
    assert cli.main(["serve", "--host", "127.0.0.1", "--port", "0", "--behind-proxy", "--quiet"]) == 2
    assert "--allowed-host" in capsys.readouterr().err
    lv = Live(tmp_path, behind_proxy=True, allowed_hosts={"portal.example.com"})
    try:
        assert lv.req("GET", "/api/metrics", {"Host": "rebound.example"})[0] == 421
        assert lv.req("GET", "/api/metrics", {"Host": "portal.example.com"})[0] == 200
        assert lv.req("GET", "/healthz", {"Host": "192.0.2.5:7451"})[0] == 200
    finally:
        lv.close()


def test_cli_serve_refuses_without_token(tmp_path, monkeypatch, capsys):
    from gnomonkit_portal import cli
    monkeypatch.setenv("GNOMONKIT_PORTAL_DIR", str(tmp_path))
    monkeypatch.delenv("GNOMONKIT_PORTAL_TOKEN", raising=False)
    assert cli.main(["serve", "--host", "0.0.0.0", "--port", "0", "--quiet"]) == 2
    assert "refusing to bind" in capsys.readouterr().err


def test_memory_auto_is_off_on_a_server_bind(tmp_path):
    cfg = server.Config(host="0.0.0.0", port=0, token="t" * 40, data_dir=tmp_path)
    assert server.State(cfg, mem_cli="mempalace-fake", mem_index="").memory is False
    cfg.memory = "on"
    assert server.State(cfg, mem_cli="mempalace-fake", mem_index="").memory is True
    cfg.memory = "on"
    assert server.State(cfg, mem_cli="", mem_index="").memory is False
    db = tmp_path / "history.db"
    db.write_bytes(b"")
    cfg.memory = "auto"
    assert server.State(cfg, mem_cli="", mem_index=str(db)).memory is False      # still a server bind
    cfg.memory = "on"
    assert server.State(cfg, mem_cli="", mem_index=str(db)).memory_source == "history"
    assert server.State(cfg, mem_cli="mempalace-fake", mem_index=str(db)).memory_source == "mempalace"


# ------------------------------------------------------------------ refresh
def _fake_collector(payload, code=0):
    def run(cmd, **kw):
        out = Path(cmd[cmd.index("--json") + 1])
        if code == 0:
            out.write_text(payload, encoding="utf-8")
        return SimpleNamespace(returncode=code, stdout="", stderr="Traceback\nValueError: /some/private/path")
    return run


def test_refresh_writes_metrics_and_state(tmp_path):
    col = tmp_path / "effectiveness.py"
    col.write_text("", encoding="utf-8")
    rec = refresh.run("nightly", collector=col, d=tmp_path, runner=_fake_collector(json.dumps(metrics())))
    assert rec["ok"] and (tmp_path / "metrics.json").is_file()
    st = refresh.state(tmp_path)
    assert st["trigger"] == "nightly" and st["ok"] and st["last_ok_at"]
    assert not refresh.running(tmp_path)


def test_failed_refresh_keeps_last_good_data_and_no_paths(tmp_path):
    col = tmp_path / "effectiveness.py"
    col.write_text("", encoding="utf-8")
    refresh.run("cli", collector=col, d=tmp_path, runner=_fake_collector(json.dumps(metrics())))
    before = (tmp_path / "metrics.json").read_text(encoding="utf-8")
    rec = refresh.run("button", collector=col, d=tmp_path, runner=_fake_collector("", code=3))
    assert not rec["ok"] and rec["error"] == "collector exit 3: ValueError"
    assert "/some/private" not in json.dumps(refresh.state(tmp_path))
    assert (tmp_path / "metrics.json").read_text(encoding="utf-8") == before
    assert refresh.state(tmp_path)["last_ok_at"]
    rec = refresh.run("cli", collector=col, d=tmp_path, runner=_fake_collector("{not json"))
    assert rec["error"] == "collector wrote unreadable JSON"


def test_refresh_runs_one_at_a_time(tmp_path):
    (tmp_path / "refresh.lock").write_text("1", encoding="utf-8")
    rec = refresh.run("cli", collector=tmp_path / "x.py", d=tmp_path, runner=_fake_collector("{}"))
    assert rec["error"] == "already running"


def test_refresh_without_collector(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "find_collector", lambda: None)
    rec = refresh.run("cli", d=tmp_path)
    assert not rec["ok"] and "collector not found" in rec["error"]


def test_config_and_data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("GNOMONKIT_PORTAL_DIR", raising=False)
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    assert paths.data_dir() == tmp_path / "gk" / "portal"
    (tmp_path / "config.json").write_text(json.dumps({"hide_titles": True, "memory": "nope", "x": 1}), encoding="utf-8")
    monkeypatch.delenv("GNOMONKIT_PORTAL_HIDE_TITLES", raising=False)
    cfg = paths.load_config(tmp_path)
    assert cfg["hide_titles"] is True and cfg["memory"] == "auto" and "x" not in cfg


# ------------------------------------------------------------------ token
def test_token_file_is_kept_and_private(tmp_path):
    p = tmp_path / "sub" / "token"
    t1, created = token.ensure(p)
    assert created and token.valid(t1)
    t2, created = token.ensure(p)
    assert t2 == t1 and not created
    if os.name == "posix":
        assert (p.stat().st_mode & 0o777) == 0o600
    assert token.generate() != token.generate()


# ------------------------------------------------------------------ memory
SAMPLE = """
============================================================
  Results for: "demo"
============================================================

  [1] demo-wing / technical
      Source: /var/data/session-one.jsonl
      Match:  cosine_sim=0.5

      first line of a result
      \u2192 second line with api_key=abc123 inside
      \u2192 and a long blob AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA end

  --------------------------------------------------------
  [2] other / general
      Source: two.jsonl
      Match:  cosine_sim=0.4

      <b>markup stays text</b>
"""


def test_memory_parse_and_mask():
    r = memory.parse(SAMPLE)
    assert [x["rank"] for x in r] == [1, 2]
    assert r[0]["source"] == "session-one.jsonl"            # file name only, never the folder
    assert "abc123" not in r[0]["snippet"] and "AAAAAAAA" not in r[0]["snippet"]
    assert r[0]["snippet"].startswith("first line")
    assert r[1]["snippet"] == "<b>markup stays text</b>"     # escaped by the page and by dumps()


def test_memory_search_passes_query_after_double_dash():
    seen = {}

    def runner(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout=SAMPLE, stderr="")
    out = memory.search("--wing\nx", cli="mempalace", runner=runner)
    assert seen["cmd"][-2:] == ["--", "--wing x"]
    assert len(out["results"]) == 2
    assert memory.search("", cli="mempalace", runner=runner)["results"] == []
    fail = memory.search("q", cli="mempalace", runner=lambda c, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
    assert fail["error"].startswith("search failed")


def _core_index(tmp_path, monkeypatch, use_fts=None):
    """A history index built by core's own code, in a temp GNOMONKIT_HOME, from synthetic records."""
    core = Path(__file__).resolve().parents[1] / "plugins" / "core"
    monkeypatch.syspath_prepend(str(core))
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    from gnomonkit_cmds import _history as H
    st = H.Store(use_fts=use_fts)
    blob = "Q" * 40
    for i, (text, project) in enumerate([
            ("we pinned the widget build to the older toolchain because the new one broke the cache " + blob,
             "demo-app"),
            ("lunch order and nothing about builds", "demo-app"),
            ("the widget toolchain moved again in another project", "other-app")]):
        rec = {"id": "r%d" % i, "origin": "transcript", "session": "5e55%04d-aaaa-bbbb-cccc-000000000000" % i,
               "project": project, "root": "", "role": "user", "ts": "2026-01-0%dT10:00:00Z" % (i + 1),
               "ref": "", "url": ""}
        st.replace_record("sessions", rec, [text])
    st.con.commit()
    st.close()
    return H.db_path()


@pytest.mark.parametrize("fts", [None, False])
def test_memory_reads_core_index_read_only(tmp_path, monkeypatch, fts):
    db = _core_index(tmp_path, monkeypatch, use_fts=fts)
    assert memory.find_index() == db
    before = Path(db).read_bytes()
    out = memory.search_index("widget toolchain cache", db)
    assert out["error"] is None and [r["rank"] for r in out["results"]] == [1, 2]
    top = out["results"][0]
    assert "pinned the widget build" in top["snippet"] and "Q" * 40 not in top["snippet"]   # masked
    assert top["where"] == "demo-app · user" and top["source"] == "session 5e550000" and top["match"] == "2026-01-01"
    assert "lunch" not in json.dumps(out)
    assert memory.search_index("", db)["results"] == []
    assert Path(db).read_bytes() == before                    # read-only: the file is unchanged
    assert memory.search_index("x", str(tmp_path / "missing.db"))["error"].startswith("no history index")


def test_memory_search_does_not_wait_on_the_indexer(tmp_path, monkeypatch):
    import sqlite3
    db = _core_index(tmp_path, monkeypatch)
    w = sqlite3.connect(db)
    try:
        w.execute("BEGIN EXCLUSIVE")                          # the index is in WAL mode: readers carry on
        w.execute("DELETE FROM chunks")
        assert memory.search_index("widget toolchain cache", db)["results"]
    finally:
        w.rollback()
        w.close()
    monkeypatch.setattr(memory, "INDEX_WAIT_S", 0.1)
    w = sqlite3.connect(db)
    try:
        w.execute("PRAGMA journal_mode = DELETE").fetchall()  # an index from before WAL: a lock blocks
        w.execute("BEGIN EXCLUSIVE")
        t0 = time.monotonic()
        out = memory.search_index("widget toolchain cache", db)
        assert out["results"] == [] and "busy" in out["error"] and time.monotonic() - t0 < 5
    finally:
        w.rollback()
        w.close()


def test_memory_tab_falls_back_to_core_index(tmp_path, monkeypatch):
    db = _core_index(tmp_path, monkeypatch)
    cfg = server.Config(port=0, data_dir=tmp_path)
    st = server.State(cfg, mem_cli="", mem_index=db)
    assert st.memory is True and st.status()["memory_source"] == "history"
    assert any("pinned the widget" in r["snippet"] for r in st.memory_search("widget")["results"])
    lv = Live(tmp_path, mem_cli="", mem_index=db)
    try:
        s = json.loads(lv.req("GET", "/api/status")[2])
        assert s["memory"] is True and s["memory_source"] == "history"
        status, _, body = lv.req("GET", "/api/memory?q=widget+toolchain+cache")
        assert status == 200 and json.loads(body)["results"][0]["where"].startswith("demo-app")
    finally:
        lv.close()


# ------------------------------------------------------------------ installer
def env_for(system, tmp, **kw):
    py = {"Windows": r"C:\Py\python.exe", "Darwin": "/usr/local/bin/python3"}.get(system, "/usr/bin/python3")
    # The service account's ids are this process's own, so the steps that run "as" it work without root.
    base = dict(system=system, home=tmp / "home", python=py, uid=501, user="demo", user_home=tmp / "home",
                etc=tmp / "etc", user_uid=getattr(os, "getuid", lambda: 1001)(), user_gid=getattr(os, "getgid", lambda: 1001)())
    if system == "Windows":
        base["pythonw"] = r"C:\Py\pythonw.exe"
    base.update(kw)
    return IP.Env(**base)


def run_installer(argv, env, runner=None, port_state="free", comes_up=True):
    """The installer with every OS call mocked: commands, the port probe and the health wait."""
    lines, calls = [], []

    def fake(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="LastTaskResult=2  LastRunTime=x", stderr="")
    code = IP.main(argv, env=env, runner=runner or fake, out=lines.append,
                   probe=lambda h, p: port_state, wait=lambda h, p, s: comes_up)
    return code, "\n".join(lines), calls


def _ps_script(cmd):
    return base64.b64decode(cmd[-1]).decode("utf-16-le")


def test_install_dry_run_windows(tmp_path):
    code, out, calls = run_installer(["--dry-run"], env_for("Windows", tmp_path))
    assert code == 0 and calls == []
    assert "Register-ScheduledTask -TaskName 'Gnomonkit Portal'" in out
    assert "-AtLogOn" in out and "-Daily -At '03:30'" in out
    assert "pythonw.exe" in out and "http://127.0.0.1:7451/" in out
    assert not (tmp_path / "home").exists()


def test_install_windows_runs_one_encoded_script(tmp_path):
    code, out, calls = run_installer(["--port", "9001"], env_for("Windows", tmp_path))
    assert code == 0 and len(calls) == 1
    assert calls[0][:2] == ["powershell.exe", "-NoProfile"] and "-EncodedCommand" in calls[0]
    script = _ps_script(calls[0])
    assert "--port 9001" in script and "New-TimeSpan -Seconds 0" in script and "-Force" in script


def test_windows_quoting_survives_quotes_and_spaces():
    assert IP.ps_quote("it's") == "'it''s'"
    assert IP.win_arg(r"C:\Program Files\x") == r'"C:\Program Files\x"'
    assert IP.win_arg('a"b') == r'"a\"b"'
    assert IP.win_arg("plain") == "plain"
    assert IP.win_arg("plain", force=True) == '"plain"'
    assert IP.win_arg("C:\\a b\\", force=True) == '"C:\\a b\\\\"'      # a trailing backslash must not eat the quote


def test_uninstall_windows(tmp_path):
    code, out, calls = run_installer(["--uninstall"], env_for("Windows", tmp_path))
    s = _ps_script(calls[0])
    assert "Unregister-ScheduledTask" in s and "'Gnomonkit Portal Refresh'" in s and "kept the data dir" in out


def test_install_dry_run_macos(tmp_path):
    code, out, calls = run_installer(["--dry-run"], env_for("Darwin", tmp_path))
    assert code == 0 and calls == []
    assert "gnomonkit.portal.plist" in out and "<key>KeepAlive</key>" in out and "StartCalendarInterval" in out
    assert "launchctl bootstrap gui/501" in out


def test_install_macos_writes_agents_and_is_idempotent(tmp_path):
    env = env_for("Darwin", tmp_path)
    code, out, calls = run_installer([], env)
    agents = tmp_path / "home" / "Library" / "LaunchAgents"
    assert code == 0 and (agents / "gnomonkit.portal.plist").is_file()
    assert ["launchctl", "bootstrap", "gui/501", str(agents / "gnomonkit.portal.plist")] in calls
    code, out, _ = run_installer([], env)
    assert "unchanged" in out and "wrote" not in out
    code, out, _ = run_installer(["--uninstall"], env)
    assert not (agents / "gnomonkit.portal.plist").exists()


def test_install_linux_user_units(tmp_path):
    env = env_for("Linux", tmp_path)
    code, out, calls = run_installer([], env)
    ud = tmp_path / "home" / ".config" / "systemd" / "user"
    unit = (ud / IP.UNIT).read_text(encoding="utf-8")
    assert code == 0 and "serve --host 127.0.0.1 --port 7451" in unit and "WantedBy=default.target" in unit
    assert "OnCalendar=*-*-* 03:30:00" in (ud / IP.TIMER).read_text(encoding="utf-8")
    assert ["systemctl", "--user", "enable", "--now", IP.UNIT, IP.TIMER] in calls
    assert "enable-linger" in out
    assert not (tmp_path / "home" / ".gnomonkit" / "portal" / "token").exists()     # loopback: no token
    code, out, _ = run_installer(["--uninstall", "--purge"], env)
    assert not (ud / IP.UNIT).exists()


def test_local_non_loopback_creates_a_token(tmp_path, monkeypatch):
    monkeypatch.delenv("GNOMONKIT_PORTAL_DIR", raising=False)
    monkeypatch.delenv("GNOMONKIT_HOME", raising=False)
    env = env_for("Linux", tmp_path)
    code, out, _ = run_installer(["--host", "0.0.0.0"], env)
    assert code == 0 and token.read(tmp_path / "home" / ".gnomonkit" / "portal" / "token")
    assert "login-url" in out


def test_install_dry_run_server(tmp_path):
    env = env_for("Linux", tmp_path, is_root=False)
    code, out, calls = run_installer(["--mode", "server", "--host", "0.0.0.0", "--dry-run"], env)
    assert code == 0 and calls == []
    assert "User=demo" in out and "ProtectSystem=strict" in out and "WantedBy=multi-user.target" in out
    assert "would run as demo:" in out and "token init" in out and "mode 0600" in out
    assert "portal.example.com" in out and "reverse_proxy 127.0.0.1:7451" in out and "proxy_pass" in out
    assert not (tmp_path / "etc").exists()


def test_server_mode_on_other_os_prints_under_dry_run(tmp_path):
    code, out, _ = run_installer(["--mode", "server", "--dry-run"], env_for("Windows", tmp_path))
    assert code == 0 and "systemd" in out
    with pytest.raises(SystemExit):
        run_installer(["--mode", "server"], env_for("Windows", tmp_path))


def test_server_mode_needs_root_and_a_user(tmp_path):
    with pytest.raises(SystemExit, match="sudo"):
        run_installer(["--mode", "server"], env_for("Linux", tmp_path, is_root=False))
    with pytest.raises(SystemExit, match="root"):
        run_installer(["--mode", "server", "--user", "root"], env_for("Linux", tmp_path, is_root=True))


def test_server_install_writes_units_and_token(tmp_path):
    env = env_for("Linux", tmp_path, is_root=True)
    seen = []

    def runner(cmd, **kw):
        seen.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    code, out, calls = run_installer(["--mode", "server", "--host", "0.0.0.0", "--allowed-host", "portal.example.com"],
                                     env, runner=runner)
    sd = tmp_path / "etc" / "systemd" / "system"
    unit = (sd / IP.UNIT).read_text(encoding="utf-8")
    assert code == 0 and "--allowed-host portal.example.com" in unit and "--behind-proxy" not in unit
    assert (["systemctl", "enable", "--now", IP.UNIT, IP.TIMER], {"capture_output": True, "text": True,
            "creationflags": getattr(IP.subprocess, "CREATE_NO_WINDOW", 0)}) in seen
    # The token is made by the service account itself, never by root writing into that account's home.
    data = tmp_path / "home" / ".gnomonkit" / "portal"
    tok_cmd, kw = next((c, k) for c, k in seen if c[-2:] == ["token", "init"])
    assert tok_cmd[1:4] == [str(IP.RUN), "--data-dir", str(data)]
    assert kw["user"] == env.user_uid and kw["group"] == env.user_gid and kw["extra_groups"] == []
    assert kw["umask"] == 0o077 and kw["env"]["HOME"] == str(tmp_path / "home") and kw["env"]["USER"] == "demo"
    assert not (data / "token").exists()                                    # root wrote nothing there


def test_server_install_never_chowns_as_root(tmp_path, monkeypatch):
    """Root changing ownership of a path the service account controls hands it any file on the machine."""
    called = []
    for name in ("chown", "lchown", "chmod"):
        if hasattr(os, name):
            monkeypatch.setattr(IP.os, name, lambda *a, **k: called.append(a))
    env = env_for("Linux", tmp_path, is_root=True)
    (tmp_path / "home").mkdir(mode=0o700)       # private, as a home folder is
    code, _, _ = run_installer(["--mode", "server", "--host", "0.0.0.0", "--allowed-host", "portal.example.com"], env)
    assert code == 0
    assert not [a for a in called if str(tmp_path / "home") in str(a[0])]


def test_behind_proxy_server_has_no_token(tmp_path):
    env = env_for("Linux", tmp_path, is_root=True)
    with pytest.raises(SystemExit, match="--allowed-host"):
        run_installer(["--mode", "server", "--behind-proxy"], env)
    code, out, _ = run_installer(["--mode", "server", "--behind-proxy", "--allowed-host", "portal.example.com"], env)
    assert "--behind-proxy" in (tmp_path / "etc" / "systemd" / "system" / IP.UNIT).read_text(encoding="utf-8")
    assert "token init" not in out
    assert not (tmp_path / "home" / ".gnomonkit" / "portal" / "token").exists()


def test_failed_command_is_reported(tmp_path):
    def bad(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="unit failed")
    code, out, _ = run_installer([], env_for("Linux", tmp_path), runner=bad)
    assert code == 1 and "FAILED" in out and "unit failed" in out


def test_systemd_quoting():
    assert IP.sd_quote("/opt/my app/run.py") == '"/opt/my app/run.py"'
    assert IP.sd_quote("100%") == '"100%%"'
    assert IP.sd_quote("plain") == "plain"


def test_bad_refresh_time():
    with pytest.raises(SystemExit):
        IP.parse_time("25:00")


# ------------------------------------------------------------------ installer: port, health, quoting (0.1.1)
def test_defaults_agree_between_installer_and_server():
    # 8765, a common default, collides with widely installed desktop software (see server.py)
    assert IP.DEFAULT_PORT == server.DEFAULT_PORT != 8765
    assert (IP.HEALTH_BODY, IP.HEALTH_HEADER) == (server.HEALTH_BODY, server.HEALTH_HEADER)


@pytest.mark.parametrize("state", ["busy", "reserved"])
@pytest.mark.parametrize("system", ["Windows", "Darwin", "Linux"])
def test_taken_port_refuses_before_registering(tmp_path, state, system):
    code, out, calls = run_installer(["--port", "7600"], env_for(system, tmp_path), port_state=state)
    assert code == 1 and calls == []                                   # nothing registered or started
    assert "--port" in out and "Nothing was installed" in out
    assert "Portal: http" not in out
    assert not (tmp_path / "home" / "Library").exists() and not (tmp_path / "home" / ".config").exists()


def test_refusal_suggests_a_port_that_is_free(tmp_path):
    lines = []
    probe = lambda h, p: "free" if p == 7605 else "busy"                 # noqa: E731
    code = IP.main(["--port", "7600"], env=env_for("Linux", tmp_path), runner=lambda *a, **k: None,
                   out=lines.append, probe=probe, wait=lambda h, p, s: True)
    assert code == 1 and "--port 7605, which is free" in "\n".join(lines)


def test_port_held_by_an_earlier_portal_is_replaced(tmp_path):
    code, out, calls = run_installer([], env_for("Windows", tmp_path), port_state="portal")
    assert code == 0 and len(calls) == 1 and "earlier portal" in out


def test_portal_that_does_not_come_up_fails_loudly_windows(tmp_path):
    code, out, calls = run_installer([], env_for("Windows", tmp_path), comes_up=False)
    assert code == 1 and "did not answer" in out and "LastTaskResult=2" in out
    assert "Get-ScheduledTaskInfo" in _ps_script(calls[-1])            # the diagnosis asked the task
    assert "Portal: http" not in out and "ok: the portal answers" not in out


@pytest.mark.parametrize("system,diag", [("Darwin", "launchctl"), ("Linux", "systemctl")])
def test_portal_that_does_not_come_up_fails_loudly_posix(tmp_path, system, diag):
    code, out, calls = run_installer([], env_for(system, tmp_path), comes_up=False)
    assert code == 1 and "did not answer" in out and calls[-1][0] == diag and "portal.log" in out


def test_dry_run_describes_the_checks(tmp_path):
    code, out, calls = run_installer(["--dry-run"], env_for("Linux", tmp_path), port_state="busy", comes_up=False)
    assert code == 0 and calls == []                                   # a dry run probes nothing
    assert "would check that 127.0.0.1:7451 is free" in out and "/healthz" in out


def test_rerun_with_another_port_replaces_the_task_windows(tmp_path):
    env = env_for("Windows", tmp_path)
    _, _, first = run_installer(["--port", "7600"], env)
    code, out, second = run_installer(["--port", "7601"], env)
    s1, s2 = _ps_script(first[0]), _ps_script(second[0])
    assert code == 0 and "--port 7600" in s1 and "--port 7601" in s2 and "--port 7600" not in s2
    # the old task is stopped, then re-registered in place under the same name
    assert s2.index("Stop-ScheduledTask") < s2.index("Register-ScheduledTask -TaskName 'Gnomonkit Portal' ")
    assert s2.count("-Force") == 2 and "ok: the portal answers on port 7601" in out


def test_rerun_with_another_port_replaces_the_unit_linux(tmp_path):
    env = env_for("Linux", tmp_path)
    run_installer(["--port", "7600"], env)
    code, out, calls = run_installer(["--port", "7601"], env)
    unit = (tmp_path / "home" / ".config" / "systemd" / "user" / IP.UNIT).read_text(encoding="utf-8")
    assert code == 0 and "--port 7601" in unit and "7600" not in unit
    assert "wrote" in out and ["systemctl", "--user", "restart", IP.UNIT] in calls


SPACE_DIR = "First " + "Last"            # a home folder name with a space


def _task_argument(script, n=1):
    import re
    args = re.findall(r"-Argument '((?:[^']|'')*)'", script)
    return args[n - 1].replace("''", "'")


def test_windows_task_argument_quotes_paths_with_spaces(tmp_path, monkeypatch):
    run = "C:\\Users\\" + SPACE_DIR + "\\plugins\\portal\\run_portal.py"
    data = "C:\\Users\\" + SPACE_DIR + "\\portal data\\"
    monkeypatch.setattr(IP, "RUN", Path(run))
    env = env_for("Windows", tmp_path, pythonw="C:\\Program Files\\Py\\pythonw.exe")
    code, out, calls = run_installer(["--data-dir", data], env)
    script = _ps_script(calls[0])
    serve, ref = _task_argument(script, 1), _task_argument(script, 2)
    assert serve.startswith('"' + run + '" --data-dir "')
    assert "SetEnvironmentVariable" not in script
    assert "-Execute '\"C:\\Program Files\\Py\\pythonw.exe\"'" in script
    if os.name == "nt":                     # split it exactly as pythonw will
        import ctypes
        from ctypes import wintypes
        f = ctypes.windll.shell32.CommandLineToArgvW
        f.argtypes, f.restype = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)], ctypes.POINTER(wintypes.LPWSTR)
        for arg, tail in ((serve, ["serve", "--host", "127.0.0.1"]), (ref, ["refresh", "--trigger", "nightly"])):
            n = ctypes.c_int()
            p = f("pythonw.exe " + arg, ctypes.byref(n))
            argv = [p[i] for i in range(n.value)]
            ctypes.windll.kernel32.LocalFree(p)
            assert argv[1:4] == [run, "--data-dir", str(Path(data))] and argv[4:7] == tail


def test_launchd_and_systemd_keep_paths_with_spaces_whole(tmp_path, monkeypatch):
    import plistlib
    import shlex
    run = "/Users/" + SPACE_DIR + "/plugins/portal/run_portal.py"
    monkeypatch.setattr(IP, "RUN", Path(run))
    data = tmp_path / "portal data"
    run_installer(["--data-dir", str(data)], env_for("Darwin", tmp_path, python="/opt/my python/bin/python3"))
    pl = plistlib.loads((tmp_path / "home" / "Library" / "LaunchAgents" / "gnomonkit.portal.plist").read_bytes())
    assert pl["ProgramArguments"][:3] == ["/opt/my python/bin/python3", Path(run).as_posix() if os.sep == "/" else str(Path(run)), "serve"]
    assert pl["EnvironmentVariables"]["GNOMONKIT_PORTAL_DIR"] == str(data)
    run_installer(["--data-dir", str(data)], env_for("Linux", tmp_path, python="/opt/my python/bin/python3"))
    unit = (tmp_path / "home" / ".config" / "systemd" / "user" / IP.UNIT).read_text(encoding="utf-8")
    exec_line = [x for x in unit.splitlines() if x.startswith("ExecStart=")][0][len("ExecStart="):]
    words = shlex.split(exec_line.replace("\\\\", "\\"), posix=True) if os.sep == "/" else None
    assert '"/opt/my python/bin/python3"' in exec_line and '"%s"' % str(Path(run)).replace("\\", "\\\\") in exec_line
    if words:
        assert words[:2] == ["/opt/my python/bin/python3", run]
    env_line = [x for x in unit.splitlines() if x.startswith("Environment=")][0]
    assert env_line.startswith('Environment="GNOMONKIT_PORTAL_DIR=')


# ------------------------------------------------------------------ the real probes, against real sockets
class _Other(threading.Thread):
    """Another program on a port: answers 401 with a JSON-RPC body, like a desktop app's local API."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                b = b'{"jsonrpc":"2.0","error":{"code":-32001,"message":"unauthorized"}}'
                self.send_response(401)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def log_message(self, *a):
                pass
        super().__init__(daemon=True)
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]

    def run(self):
        self.srv.serve_forever()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()


def test_healthz_carries_the_portal_signature(live):
    st, h, body = live.req("GET", "/healthz")
    assert st == 200 and h[server.HEALTH_HEADER] and body.startswith(b"gnomonkit-portal ")
    assert live.req("GET", "/healthz", {"Host": "elsewhere.example"})[0] == 200     # exempt from the Host check


def test_probes_tell_the_portal_from_another_program(live):
    other = _Other()
    other.start()
    try:
        assert IP.is_portal("127.0.0.1", live.port) is True
        assert IP.is_portal("127.0.0.1", other.port) is False           # a 401 answer is not the portal
        assert IP.probe_port("127.0.0.1", live.port) == "portal"
        assert IP.probe_port("127.0.0.1", other.port) == "busy"
        assert IP.wait_portal("127.0.0.1", other.port, 0.6) is False
        assert IP.wait_portal("127.0.0.1", live.port, 0.6) is True
    finally:
        other.stop()
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    assert IP.probe_port("127.0.0.1", free) == "free"


def test_cli_data_dir_option(tmp_path, capsys):
    from gnomonkit_portal import cli
    old = os.environ.get("GNOMONKIT_PORTAL_DIR")
    try:
        assert cli.main(["--data-dir", str(tmp_path / "d d"), "status"]) == 0
        assert json.loads(capsys.readouterr().out)["data_dir"] == str(tmp_path / "d d")
    finally:
        if old is None:
            os.environ.pop("GNOMONKIT_PORTAL_DIR", None)
        else:
            os.environ["GNOMONKIT_PORTAL_DIR"] = old


# ------------------------------------------------------------------ hardening (portal 0.4.0)
POSIX = os.name == "posix"
posix_only = pytest.mark.skipif(not POSIX, reason="file modes, owners and symlinks are checked on POSIX only; "
                                                  "on Windows the profile folder's ACL keeps the files private")


def _raw(port, data, timeout=5):
    """Send raw bytes and read until the server closes (or goes quiet)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    out = b""
    try:
        s.sendall(data)
        while True:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            out += chunk
    finally:
        s.close()
    return out


def _status_line(resp: bytes) -> str:
    return resp.split(b"\r\n", 1)[0].decode("ascii", "replace")


def _put_metrics(d, raw, i):
    """Write metrics.json with a distinct mtime, so the server's mtime-keyed cache sees each write."""
    p = d / "metrics.json"
    p.write_text(raw, encoding="utf-8")
    os.utime(p, ns=(i * 10 ** 9, i * 10 ** 9))


@posix_only
def test_symlinked_token_is_refused(tmp_path, monkeypatch, capsys):
    d = tmp_path / "data"
    paths.private_dir(d)
    real = tmp_path / "planted"
    real.write_text(token.generate(), encoding="utf-8")
    os.symlink(real, d / "token")
    with pytest.raises(token.UnsafeToken, match="symlink"):
        token.read(d / "token")
    with pytest.raises(token.UnsafeToken):
        token.ensure(d / "token")
    os.unlink(d / "token")
    os.symlink(tmp_path / "missing", d / "token")                 # a dangling link is not "no token"
    monkeypatch.setenv("GNOMONKIT_PORTAL_DIR", str(d))
    monkeypatch.delenv("GNOMONKIT_PORTAL_TOKEN", raising=False)
    assert cli.main(["login-url"]) == 2 and "symlink" in capsys.readouterr().err


@posix_only
def test_data_folder_and_files_are_private(tmp_path, monkeypatch):
    d = tmp_path / "a" / "b"
    paths.private_dir(d)
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700 and stat.S_IMODE(os.stat(d.parent).st_mode) == 0o700
    paths.write_json_atomic(d / "state.json", {"ok": True})
    assert stat.S_IMODE(os.stat(d / "state.json").st_mode) == 0o600
    with paths.open_private_append(d / "portal.log") as fh:
        fh.write("x\n")
    assert stat.S_IMODE(os.stat(d / "portal.log").st_mode) == 0o600
    tok, _ = token.ensure(d / "token")
    assert stat.S_IMODE(os.stat(d / "token").st_mode) == 0o600
    os.chmod(d / "token", 0o644)                                  # a token from before 0.4.0
    assert token.read(d / "token") == tok and stat.S_IMODE(os.stat(d / "token").st_mode) == 0o600
    loose = tmp_path / "loose"
    loose.mkdir()
    os.chmod(loose, 0o755)
    paths.private_dir(loose)
    assert stat.S_IMODE(os.stat(loose).st_mode) == 0o700           # readable by others: tightened
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o777)
    with pytest.raises(paths.UnsafePath, match="chmod 700") as ei:  # writable by others: refused, never used
        paths.private_dir(shared)
    assert "without POSIX permissions" in str(ei.value)            # where chmod cannot help, it says so
    monkeypatch.setenv("GNOMONKIT_PORTAL_DIR", str(shared))
    assert cli.main(["refresh"]) == 2
    real_euid = os.geteuid()
    monkeypatch.setattr(paths.os, "geteuid", lambda: real_euid + 4242)
    with pytest.raises(paths.UnsafePath, match="another account"):
        paths.private_dir(loose)


@posix_only
def test_planted_links_at_temp_names_are_not_followed(tmp_path):
    d = tmp_path / "data"
    paths.private_dir(d)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    for name in ("metrics.json.tmp", "state.json.tmp", ".metrics.json.tmp", "metrics.tmp"):
        os.symlink(victim, d / name)
    col = tmp_path / "effectiveness.py"
    col.write_text("", encoding="utf-8")
    rec = refresh.run("cli", collector=col, d=d, runner=_fake_collector(json.dumps(metrics())))
    assert rec["ok"] and victim.read_text(encoding="utf-8") == "keep"
    assert json.loads((d / "metrics.json").read_text(encoding="utf-8"))["schema"] == 1
    assert stat.S_IMODE(os.stat(d / "metrics.json").st_mode) == 0o600
    os.unlink(d / "state.json")
    os.symlink(victim, d / "state.json")                           # a link at the final name is replaced
    paths.write_json_atomic(d / "state.json", {"ok": True})
    assert victim.read_text(encoding="utf-8") == "keep" and not os.path.islink(d / "state.json")
    assert not [p for p in d.iterdir() if p.name.startswith(".refresh-")]     # the work folder is removed


def test_memory_auto_only_for_plain_loopback(tmp_path):
    base = dict(port=0, data_dir=tmp_path)
    assert server.State(server.Config(**base), mem_cli="mempalace-fake").memory is True
    assert server.State(server.Config(token="t" * 40, **base), mem_cli="mempalace-fake").memory is False
    proxy = server.Config(behind_proxy=True, allowed_hosts={"portal.example.com"}, **base)
    assert server.State(proxy, mem_cli="mempalace-fake").memory is False
    assert server.State(server.Config(token="t" * 40, memory="on", **base), mem_cli="mempalace-fake").memory is True


def test_memory_auto_with_the_history_index_only_for_plain_loopback(tmp_path, monkeypatch):
    db = _core_index(tmp_path, monkeypatch)
    base = dict(port=0, data_dir=tmp_path)
    st = server.State(server.Config(**base), mem_cli="", mem_index=db)
    assert st.memory is True and st.status()["memory_source"] == "history"
    tok = server.State(server.Config(token="t" * 40, **base), mem_cli="", mem_index=db)
    assert tok.memory is False and tok.status()["memory_source"] is None
    proxy = server.Config(behind_proxy=True, allowed_hosts={"portal.example.com"}, **base)
    assert server.State(proxy, mem_cli="", mem_index=db).memory is False
    on = server.State(server.Config(token="t" * 40, memory="on", **base), mem_cli="", mem_index=db)
    assert on.memory is True and on.status()["memory_source"] == "history"


def test_memory_index_source_name_is_capped(tmp_path, monkeypatch):
    db = _core_index(tmp_path, monkeypatch)
    from gnomonkit_cmds import _history as H
    st = H.Store()
    long_ref = "work item " + " ".join(["widget"] * 60)
    rec = {"id": "r9", "origin": "work", "session": "", "project": "demo-app", "root": "", "role": "",
           "ts": "2026-01-09T10:00:00Z", "ref": long_ref, "url": ""}
    st.replace_record("sessions", rec, ["the capped widget source line"])
    st.con.commit()
    st.close()
    out = memory.search_index("capped widget source", db)
    hit = [r for r in out["results"] if "capped widget" in r["snippet"]]
    assert hit and len(hit[0]["source"]) == 120 and long_ref.startswith(hit[0]["source"])


def test_nan_and_infinity_never_reach_the_page(tmp_path):
    lv = Live(tmp_path)
    try:
        m = metrics()
        m["usage"]["total"]["est_cost_usd"] = float("nan")
        m["usage"]["months"][0]["api_equiv_usd"] = float("inf")
        m["subagents"]["spend"]["total"] = float("-inf")
        _put_metrics(tmp_path, json.dumps(m), 1)                  # json writes the NaN and Infinity literals
        st, _, body = lv.req("GET", "/api/metrics")
        assert st == 200 and b"NaN" not in body and b"Infinity" not in body
        u = json.loads(body)["sections"]["usage"]
        assert u["total"].get("est_cost_usd") is None and u["total"]["turns"] == 40      # the rest still shows
    finally:
        lv.close()


def test_malformed_metrics_and_state_give_an_empty_200(tmp_path):
    lv = Live(tmp_path)
    try:
        weird = {"usage": {"sessions": [{"title": 5, "total": "x", "session": 7}], "months": "x", "projects": 3},
                 "budgets": {"projects": {"a": 1}}, "titles": [1],
                 "subagents": {"spend": {"total": "NaN"}, "not_worth_it_spawns": 7, "by_project": [1]},
                 "main_context": {"per_session": "x"}, "hooks": {"interlock": {"outcomes": [1]}}}
        cases = ["[1, 2, 3]", "{not json", "[" * 50_000 + "]" * 50_000, json.dumps(weird), "null"]
        for i, raw in enumerate(cases, 1):
            _put_metrics(tmp_path, raw, i)
            st, _, body = lv.req("GET", "/api/metrics")
            v = json.loads(body)
            assert st == 200 and isinstance(v["sections"], dict), raw[:20]
            if i in (1, 2, 3, 5):
                assert v["empty"] is True, raw[:20]
        (tmp_path / "state.json").write_text("[1]", encoding="utf-8")
        assert lv.req("GET", "/api/status")[0] == 200
        (tmp_path / "state.json").write_text(json.dumps({"error": {"x": "/srv/" + "private"}, "ok": [1]}),
                                             encoding="utf-8")
        st, _, body = lv.req("GET", "/api/status")
        assert st == 200 and b"private" not in body
    finally:
        lv.close()


def test_batch_wrappers_are_never_run(tmp_path, monkeypatch):
    """cmd.exe parses a .bat or .cmd file's arguments as commands, so a query could run one."""
    for name in ("mempalace.bat", "mempalace.CMD"):
        f = tmp_path / name
        f.write_text("@echo off\n", encoding="utf-8")
        monkeypatch.setenv("GNOMONKIT_MEMPALACE_CLI", str(f))
        assert memory.find_cli() is None
        ran = []
        out = memory.search("hello & echo x", cli=str(f), runner=lambda c, **k: ran.append(c))
        assert out["error"] == memory.BATCH_REFUSED and ran == []


def test_memory_match_field_is_masked():
    secret = "api_" + "key=" + "abc123"
    r = memory.parse(SAMPLE.replace("cosine_sim=0.5", "cosine_sim=0.5 " + secret))
    assert "abc123" not in json.dumps(r)


def test_titles_lose_bidi_and_zero_width_characters():
    raw = "safe‮txt.exe​⁦x\u0007y﻿"
    assert view.text(raw) == "safetxt.exex y"
    m = metrics()
    m["usage"]["sessions"][0]["title"] = raw
    assert view.public_view(m)["sections"]["usage"]["sessions"][0]["title"] == "safetxt.exex y"


def test_error_class_keeps_no_path():
    assert refresh.error_class("Traceback\nValueError: /some/private/path") == "ValueError"
    assert refresh.error_class("json.decoder.JSONDecodeError: Expecting value") == "json.decoder.JSONDecodeError"
    assert refresh.error_class("Traceback\n/srv/" + "someone/run.py: failed") == ""
    assert refresh.error_class("KeyError") == "KeyError" and refresh.error_class("") == ""
    assert refresh.error_class("x" * 200 + "Error: y") == ""


def test_bad_request_bodies_are_refused_at_once(live):
    origin = "http://127.0.0.1:%d" % live.port
    for hdr, code in (("Content-Length: -1", 400), ("Content-Length: abc", 400), ("Content-Length: +5", 400),
                      ("Content-Length: 99999999999", 400), ("Transfer-Encoding: chunked", 400),
                      ("Content-Length: 5\r\nContent-Length: 6", 400), ("Content-Length: 5000", 413)):
        t0 = time.time()
        resp = _raw(live.port, ("POST /api/refresh HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nOrigin: %s\r\n%s\r\n\r\n"
                                % (live.port, origin, hdr)).encode("ascii"))
        assert (" %d " % code) in _status_line(resp), (hdr, resp[:60])
        assert b"Connection: close" in resp
        assert time.time() - t0 < 3, hdr                         # answered and closed, not left to time out
    assert live.refreshes == []


def test_connection_cap_answers_busy(tmp_path):
    lv = Live(tmp_path, max_connections=2)
    idle = []
    try:
        for _ in range(2):
            idle.append(socket.create_connection(("127.0.0.1", lv.port), timeout=5))
        time.sleep(0.5)                                            # both accepted, each holding a slot
        # The 503 goes out on accept, before anything is read. Sending nothing leaves no unread bytes on the
        # server's side, so its close is a clean FIN and never a reset that could drop the answer (Windows).
        assert " 503 " in _status_line(_raw(lv.port, b""))
        for s in idle:
            s.close()
        deadline = time.time() + 5
        while lv.req("GET", "/healthz")[0] != 200:                 # the slots come back when they close
            assert time.time() < deadline
            time.sleep(0.1)
    finally:
        for s in idle:
            s.close()
        lv.close()
    assert server.TIMEOUT <= 10 and server.MAX_CONNECTIONS <= 64


def test_error_pages_carry_the_security_headers(live):
    resp = _raw(live.port, b"BREW /pot?<b>x</b> HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    head, _, body = resp.partition(b"\r\n\r\n")
    assert " 501 " in _status_line(resp)
    assert b"X-Content-Type-Options: nosniff" in head and b"Content-Security-Policy: default-src 'none'" in head
    assert b"BREW" not in body and b"<b>" not in body                # the request is never echoed
    for line in (b"GARBAGE", b"GET / HTTP/1.1 extra", b"GET / NOTHTTP"):   # bad request lines, any Python
        resp = _raw(live.port, line + b"\r\n\r\n")
        assert " 400 " in _status_line(resp) and b"X-Frame-Options: DENY" in resp and line not in resp, line
    resp = _raw(live.port, b"GET /\r\n\r\n")                          # HTTP/0.9: no headers could be sent
    assert " 400 " in _status_line(resp) and b"Content-Security-Policy" in resp and b"<html" not in resp.lower()


def test_session_cookie_is_not_the_token_and_expires(tmp_path):
    tok = token.generate()
    now = 1_900_000_000
    v = server.session_value(tok, now).encode("ascii")
    assert tok.encode() not in v
    assert server.session_ok(tok, v, now=now + 60)
    assert not server.session_ok(tok, v, now=now + server.SESSION_MAX_AGE + 1)
    assert not server.session_ok(tok, v, now=now - 3600)                   # issued in the future
    assert not server.session_ok(token.generate(), v, now=now)             # made with another token
    assert not server.session_ok(tok, v[:-1] + (b"0" if v[-1:] != b"0" else b"1"), now=now)
    assert not server.session_ok(tok, tok.encode(), now=now)               # the raw token is no cookie
    assert server.SESSION_MAX_AGE <= 7 * 86400
    lv = Live(tmp_path, token=tok)
    try:
        _, h, _ = lv.req("GET", "/?token=" + tok)
        ck = h["Set-Cookie"]
        assert tok not in ck and "Max-Age=%d" % server.SESSION_MAX_AGE in ck
        cookie = {"Cookie": ck.split(";")[0]}
        assert lv.req("GET", "/api/metrics", cookie)[0] == 200
        assert lv.req("GET", "/api/metrics", {"Cookie": "%s=%s" % (server.COOKIE, tok)})[0] == 401
        st, h, _ = lv.req("GET", "/logout", cookie)
        assert st == 303 and h["Location"] == "/" and "Max-Age=0" in h["Set-Cookie"]
        assert server.COOKIE + "=;" in h["Set-Cookie"]
    finally:
        lv.close()


# ------------------------------------------------------------------ installer hardening
@posix_only
def test_hand_over_dir_refuses_links_and_foreign_folders(tmp_path):
    me, grp = os.getuid(), os.getgid()
    other = me + 4242
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    os.symlink(target, link)
    with pytest.raises(IP.Unsafe, match="symlink"):
        IP.hand_over_dir(link, me, grp)
    with pytest.raises(OSError):
        IP._fd_hand_over(link, me, grp)                            # O_NOFOLLOW: never lands on the link's target
    assert IP.hand_over_dir(target, me, grp) == "ok"
    with pytest.raises(IP.Unsafe, match="another account"):
        IP.hand_over_dir(target, other, grp)                       # a folder of a third account
    own = tmp_path / "own"
    own.mkdir()
    os.chmod(own, 0o700)
    assert IP.hand_over_dir(own / "portal", me, grp) == "user"
    assert not (own / "portal").exists()                           # the account makes it itself
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o777)
    with pytest.raises(IP.Unsafe, match="safely"):
        IP.hand_over_dir(shared / "portal", me, grp)
    assert IP._root_controlled(tmp_path) is False                  # a test runner's folders are not root's


SVC, OTHER = 4242, 4343                    # the service account and a third account, as root would see them


def _root_view(monkeypatch, tmp, owners=(), modes=()):
    """IP._lstat as root sees a server: every folder down to tmp is root's own (mode 755), and so is
    everything inside tmp unless owners gives it another uid (modes, another mode). The file type stays
    the real one, so a symlink made in tmp is a symlink. Also records the hand-overs instead of chowning."""
    tmp = Path(os.path.abspath(tmp))
    owners = {Path(os.path.abspath(k)): v for k, v in dict(owners).items()}
    modes = {Path(os.path.abspath(k)): v for k, v in dict(modes).items()}
    real = os.lstat

    def fake(p):
        p = Path(os.path.abspath(p))
        st = real(p)
        if p == tmp or p in tmp.parents:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
        kind = stat.S_IFMT(st.st_mode)
        mode = 0o777 if kind == stat.S_IFLNK else 0o755 if kind == stat.S_IFDIR else 0o644
        return SimpleNamespace(st_mode=kind | modes.get(p, mode), st_uid=owners.get(p, 0))
    monkeypatch.setattr(IP, "_lstat", fake)
    handed = []
    monkeypatch.setattr(IP, "_fd_hand_over", lambda d, u, g: handed.append((Path(d), u, g)))
    return handed


def _no_runs(cmd, **kw):
    raise AssertionError("nothing may run: %r" % (cmd,))


def _svc(home):
    return {"uid": SVC, "gid": SVC, "name": "svc", "home": str(home)}


def test_root_never_follows_a_symlinked_parent(tmp_path, monkeypatch):
    """The service account owns its home, so it can make ~/.gnomonkit a symlink to a folder only root may
    change. Root must then neither create nor hand over anything there, nor delete through the link."""
    locked = tmp_path / "locked"                                   # root's own, e.g. a system config folder
    (locked / "portal").mkdir(parents=True)
    (locked / "portal" / "keep.conf").write_text("root's file", encoding="utf-8")
    home = tmp_path / "home" / "svc"
    home.mkdir(parents=True)
    link = home / ".gnomonkit"
    os.symlink(locked, link, target_is_directory=True)
    handed = _root_view(monkeypatch, tmp_path, owners={home: SVC, link: SVC})
    assert IP._root_controlled(locked) is True and IP._root_controlled(link) is False
    with pytest.raises(IP.Unsafe, match="symlink"):                 # hand-over: the folder exists behind the link
        IP.hand_over_dir(link / "portal", SVC, SVC)
    with pytest.raises(IP.Unsafe, match="symlink"):                 # create: nothing there yet
        IP.hand_over_dir(link / "fresh", SVC, SVC)
    assert handed == [] and not (locked / "fresh").exists()
    with pytest.raises(IP.Unsafe, match="never follows"):           # purge, as root
        IP.purge_dir(link / "portal", _svc(home), runner=_no_runs, is_root=True)
    with pytest.raises(IP.Unsafe):
        IP.purge_dir(link / "portal", None, runner=_no_runs, is_root=True)
    with pytest.raises(IP.Unsafe, match="symlink"):                 # the data folder itself a link
        IP.purge_dir(link, _svc(home), runner=_no_runs, is_root=True)
    assert (locked / "portal" / "keep.conf").read_text(encoding="utf-8") == "root's file"
    # The same refusal through the installer: server-mode uninstall with --purge.
    monkeypatch.setattr(IP.os, "geteuid", lambda: 0, raising=False)
    env = env_for("Linux", tmp_path, is_root=True, user="svc", user_uid=SVC, user_gid=SVC, user_home=home)
    code, out, _ = run_installer(["--mode", "server", "--uninstall", "--purge", "--data-dir", str(link / "portal")],
                                 env)
    assert code != 0 and "FAILED" in out and "never follows" in out and "Traceback" not in out
    assert (locked / "portal" / "keep.conf").is_file()


def test_root_prepares_and_purges_only_where_it_is_safe(tmp_path, monkeypatch):
    var = tmp_path / "var" / "lib"                                 # root's own all the way down
    var.mkdir(parents=True)
    home = tmp_path / "home" / "svc"
    (home / ".gk").mkdir(parents=True)
    (home / "rootmade").mkdir()
    others = tmp_path / "home" / "other"
    others.mkdir()
    shared = tmp_path / "shared"
    shared.mkdir()
    (var / "made").mkdir()
    handed = _root_view(monkeypatch, tmp_path, owners={home: SVC, home / ".gk": SVC, others: OTHER},
                        modes={shared: 0o1777})
    assert IP.hand_over_dir(var / "gnomonkit-portal", SVC, 7) == "created"
    assert (var / "gnomonkit-portal").is_dir()
    assert IP.hand_over_dir(var / "made", SVC, 7) == "handed"
    assert handed == [(var / "gnomonkit-portal", SVC, 7), (var / "made", SVC, 7)]
    assert IP.hand_over_dir(home / ".gk", SVC, 7) == "ok"
    assert IP.hand_over_dir(home / ".gnomonkit" / "portal", SVC, 7) == "user"
    assert not (home / ".gnomonkit").exists()                      # the account makes it itself
    with pytest.raises(IP.Unsafe, match="another account"):
        IP.hand_over_dir(others / "portal", SVC, 7)
    with pytest.raises(IP.Unsafe, match="another account can change"):
        IP.hand_over_dir(home / "rootmade", SVC, 7)                # root's folder, in the account's home
    with pytest.raises(IP.Unsafe, match="safely"):
        IP.hand_over_dir(shared / "portal", SVC, 7)                # others can write above it
    assert len(handed) == 2 and not (shared / "portal").exists()
    # Purge: the account deletes its own folder as itself, with nothing but its own rights.
    seen = []

    def runner(cmd, **kw):
        seen.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    assert IP.purge_dir(home / ".gk", _svc(home), runner=runner, is_root=True) == "deleted as svc"
    (cmd, kw), = seen
    assert cmd == [sys.executable, "-I", "-c", IP.RMTREE, str(home / ".gk")]
    assert kw["user"] == SVC and kw["group"] == SVC and kw["extra_groups"] == [] and kw["umask"] == 0o077
    with pytest.raises(IP.Unsafe, match="could not delete"):
        IP.purge_dir(home / ".gk", _svc(home), is_root=True,
                     runner=lambda c, **k: SimpleNamespace(returncode=1, stdout="", stderr=""))
    # Root deletes only under folders that are all its own, and only with a link-safe rmtree.
    monkeypatch.setattr(IP.shutil.rmtree, "avoids_symlink_attacks", False)
    assert IP.purge_dir(var / "made", _svc(home), runner=runner, is_root=True) == "deleted as svc"
    monkeypatch.setattr(IP.shutil.rmtree, "avoids_symlink_attacks", True)
    assert IP.purge_dir(var / "made", _svc(home), runner=_no_runs, is_root=True) == "deleted"
    assert not (var / "made").exists()
    assert IP.purge_dir(var / "made", _svc(home), runner=_no_runs, is_root=True) == "gone"


def test_local_purge_deletes_a_folder_never_a_link(tmp_path):
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "f").write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    os.symlink(keep, link, target_is_directory=True)
    with pytest.raises(IP.Unsafe, match="symlink"):
        IP.purge_dir(link, is_root=False)
    assert (keep / "f").is_file()
    mine = tmp_path / "mine"
    (mine / "sub").mkdir(parents=True)
    assert IP.purge_dir(mine, is_root=False) == "deleted" and not mine.exists()
    assert IP.purge_dir(mine, is_root=False) == "gone"


def test_dry_run_purge_says_who_deletes(tmp_path):
    code, out, calls = run_installer(["--mode", "server", "--uninstall", "--purge", "--dry-run", "--user", "demo"],
                                     env_for("Linux", tmp_path, is_root=False))
    assert code == 0 and calls == []
    assert "would delete" in out and "as demo unless every folder above it is root's own" in out


def test_local_token_refusal_is_a_message_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.delenv("GNOMONKIT_PORTAL_DIR", raising=False)
    monkeypatch.delenv("GNOMONKIT_HOME", raising=False)
    d = tmp_path / "home" / ".gnomonkit" / "portal"
    paths.private_dir(d)
    os.symlink(tmp_path / "planted", d / "token")
    code, out, calls = run_installer(["--host", "0.0.0.0"], env_for("Linux", tmp_path))
    assert code != 0 and "FAILED: refusing the token path" in out and "Nothing more was installed" in out
    assert calls == [] and not (tmp_path / "planted").exists()


@posix_only
def test_fd_hand_over_sets_the_mode_on_the_folder_itself(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    os.chmod(d, 0o755)
    IP._fd_hand_over(d, os.getuid(), os.getgid())                  # to this account: allowed without root
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700


def test_installed_jobs_use_a_private_umask_and_carry_the_snapshot(tmp_path):
    snap = tmp_path / "www" / "portal.html"
    want = os.path.abspath(snap)
    code, out, _ = run_installer(["--dry-run", "--snapshot", str(snap)], env_for("Darwin", tmp_path / "mac"))
    assert code == 0 and "<key>Umask</key>" in out and "<integer>63</integer>" in out and "--snapshot" in out
    code, out, _ = run_installer(["--snapshot", str(snap)], env_for("Linux", tmp_path / "lin"))
    ud = tmp_path / "lin" / "home" / ".config" / "systemd" / "user"
    ref = (ud / IP.UNIT_REFRESH).read_text(encoding="utf-8")
    assert code == 0 and "UMask=0077" in ref and "--snapshot " + IP.sd_quote(want) in ref
    assert "UMask=0077" in (ud / IP.UNIT).read_text(encoding="utf-8")

    def ok(cmd, **kw):
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    code, out, _ = run_installer(["--mode", "server", "--host", "0.0.0.0", "--allowed-host", "portal.example.com",
                                  "--snapshot", str(snap)], env_for("Linux", tmp_path / "srv", is_root=True),
                                 runner=ok)
    unit = (tmp_path / "srv" / "etc" / "systemd" / "system" / IP.UNIT_REFRESH).read_text(encoding="utf-8")
    rw = next(line for line in unit.splitlines() if line.startswith("ReadWritePaths="))
    assert code == 0 and IP.sd_quote(os.path.dirname(want)) in rw and "UMask=0077" in unit
    code, out, calls = run_installer(["--snapshot", str(snap)], env_for("Windows", tmp_path / "win"))
    script = _ps_script(calls[0])
    assert code == 0 and "--snapshot" in script and want in script


def test_hide_titles_reaches_the_nightly_snapshot(tmp_path):
    """--hide-titles hides titles in the nightly snapshot too, not only on the live page: the snapshot is the
    copy meant for a static host."""
    snap = tmp_path / "www" / "portal.html"
    assert IP.refresh_args(SimpleNamespace(snapshot=str(snap), hide_titles=True))[-1] == "--hide-titles"
    assert "--hide-titles" not in IP.refresh_args(SimpleNamespace(snapshot=str(snap), hide_titles=False))
    code, _, _ = run_installer(["--snapshot", str(snap), "--hide-titles"], env_for("Linux", tmp_path / "lin"))
    ud = tmp_path / "lin" / "home" / ".config" / "systemd" / "user"
    ref = next(line for line in (ud / IP.UNIT_REFRESH).read_text(encoding="utf-8").splitlines()
               if line.startswith("ExecStart="))
    assert code == 0 and " refresh --trigger nightly " in ref and "--snapshot" in ref and "--hide-titles" in ref
    code, _, _ = run_installer(["--mode", "server", "--host", "0.0.0.0", "--allowed-host", "portal.example.com",
                                "--snapshot", str(snap), "--hide-titles"],
                               env_for("Linux", tmp_path / "srv", is_root=True),
                               runner=lambda c, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    unit = (tmp_path / "srv" / "etc" / "systemd" / "system" / IP.UNIT_REFRESH).read_text(encoding="utf-8")
    assert code == 0 and "--hide-titles" in unit
    code, out, _ = run_installer(["--dry-run", "--snapshot", str(snap), "--hide-titles"], env_for("Darwin", tmp_path))
    assert code == 0 and out.count("<string>--hide-titles</string>") == 2         # serve and the nightly refresh
    code, _, calls = run_installer(["--snapshot", str(snap), "--hide-titles"], env_for("Windows", tmp_path / "win"))
    assert code == 0 and _ps_script(calls[0]).count("--hide-titles") == 2


# ------------------------------------------------------------------ snapshot
def _snap_data(tmp_path):
    d = tmp_path / "data"
    d.mkdir(mode=0o700)
    (d / "metrics.json").write_text(json.dumps(metrics()), encoding="utf-8")
    return d


def test_snapshot_is_self_contained_and_makes_no_request(tmp_path):
    d = _snap_data(tmp_path)
    html = snapshot.build(d, now=1_800_000_000)
    assert "://" not in html                                       # no URL at all: nothing to load or send to
    assert "__NONCE__" not in html and "nonce=" not in html
    csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', html).group(1)
    for part in ("default-src 'none'", "connect-src 'none'", "form-action 'none'", "base-uri 'none'"):
        assert part in csp, part
    script = re.search(r"<script>(.*?)</script>", html, re.S).group(1)
    style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
    for body in (script, style):
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode("ascii")
        assert "'sha256-%s'" % digest in csp
    data = re.search(r'<script type="application/json" id="gk-snapshot">(.*?)</script>', html, re.S).group(1)
    assert "<" not in data and ">" not in data and "&" not in data    # no title can close the element
    assert html.count("</script>") == 2
    blob = json.loads(data)
    assert set(blob) == {"status", "metrics"}
    assert blob["metrics"]["sections"]["usage"]["sessions"][0]["title"] == XSS
    assert blob["status"]["auth"] == "snapshot" and blob["status"]["memory"] is False
    assert blob["status"]["snapshot_at"].startswith("2027-01-15")
    for gone in ('id="t-memory"', 'id="p-memory"', 'id="mform"', 'id="refresh"', 'id="signout"'):
        assert gone not in html, gone
    assert SECRET_DESC not in html and CWD not in html and "home-of-someone" not in html
    assert "alert(1)" not in snapshot.build(d, hide_titles=True)
    assert json.loads(re.search(r'id="gk-snapshot">(.*?)</script>', snapshot.build(tmp_path / "none"),
                                re.S).group(1))["metrics"]["empty"] is True


def test_snapshot_write_is_private_and_never_clobbers(tmp_path, monkeypatch):
    d = _snap_data(tmp_path)
    site = tmp_path / "site"
    out = site / "portal.html"
    with pytest.raises(snapshot.SnapshotRefused, match="does not exist"):
        snapshot.write(out, d)
    site.mkdir()
    snapshot.write(out, d)
    assert snapshot.is_snapshot(out)
    if POSIX:
        assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
    with pytest.raises(snapshot.SnapshotRefused, match="--force"):
        snapshot.write(out, d)
    snapshot.write(out, d, replace_snapshot=True)                  # the nightly refresh replaces its own file
    snapshot.write(out, d, force=True)
    other = site / "index.html"
    other.write_text("<html>another page</html>", encoding="utf-8")
    with pytest.raises(snapshot.SnapshotRefused):
        snapshot.write(other, d, replace_snapshot=True)
    assert other.read_text(encoding="utf-8") == "<html>another page</html>"
    with pytest.raises(snapshot.SnapshotRefused, match="folder"):
        snapshot.write(site, d, force=True)
    assert sorted(p.name for p in site.iterdir()) == ["index.html", "portal.html"]      # no temp file left
    if POSIX:
        os.chmod(out, 0o644)                                       # its owner let a web server read it
        by_name = []
        monkeypatch.setattr(snapshot.os, "chmod", lambda *a, **k: by_name.append(a))
        snapshot.write(out, d, replace_snapshot=True)
        assert stat.S_IMODE(os.stat(out).st_mode) == 0o644
        assert by_name == []               # set on the temp file before the swap, never on the path afterwards
        monkeypatch.undo()
        link = site / "link.html"
        os.symlink(out, link)
        with pytest.raises(snapshot.SnapshotRefused):
            snapshot.write(link, d, replace_snapshot=True)


def test_cli_snapshot_and_refresh_snapshot(tmp_path, monkeypatch, capsys):
    d = _snap_data(tmp_path)
    monkeypatch.setenv("GNOMONKIT_PORTAL_DIR", str(d))
    out = tmp_path / "portal.html"
    assert cli.main(["snapshot", "--out", str(out)]) == 0
    assert cli.main(["snapshot", "--out", str(out)]) == 2 and "--force" in capsys.readouterr().err
    assert cli.main(["snapshot", "--out", str(out), "--force", "--hide-titles"]) == 0
    assert "alert(1)" not in out.read_text(encoding="utf-8")
    col = tmp_path / "fake_collector.py"
    col.write_text("import sys\nDATA = %r\nout = sys.argv[sys.argv.index('--json') + 1]\n"
                   "open(out, 'w', encoding='utf-8').write(DATA)\n" % json.dumps(metrics()), encoding="utf-8")
    nightly = tmp_path / "nightly.html"
    assert cli.main(["refresh", "--collector", str(col), "--snapshot", str(nightly)]) == 0
    assert snapshot.is_snapshot(nightly) and "alert(1)" in nightly.read_text(encoding="utf-8")
    assert cli.main(["refresh", "--collector", str(col), "--snapshot", str(nightly), "--hide-titles"]) == 0
    assert "alert(1)" not in nightly.read_text(encoding="utf-8")   # what the installer runs for --hide-titles
    assert cli.main(["refresh", "--collector", str(col), "--snapshot", str(nightly)]) == 0
    page = tmp_path / "index.html"
    page.write_text("mine", encoding="utf-8")
    assert cli.main(["refresh", "--collector", str(col), "--snapshot", str(page)]) == 1
    assert page.read_text(encoding="utf-8") == "mine"
