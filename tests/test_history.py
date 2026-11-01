"""Core's local history index: ingest, scrubbing, search, exclusions, forget and the command.

Synthetic data only: transcripts are written under pytest's tmp_path from made-up fragments, with
CLAUDE_CONFIG_DIR and GNOMONKIT_HOME pointed there, so the real ~/.claude is never read. Secrets for
the scrubber are built from pieces at run time.
"""
import hashlib
import json
import os
import sqlite3
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "plugins", "core")
sys.path.insert(0, CORE)

import gnomonkit  # noqa: E402
from gnomonkit_cmds import _common as C  # noqa: E402
from gnomonkit_cmds import _history as H  # noqa: E402
from gnomonkit_cmds import _scrub as S  # noqa: E402

FTS = H.fts5_available()
NO_MEMPALACE = {"installed": False, "mcp": False, "where": []}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("CLAUDE_PLUGIN_OPTION_") or k.startswith("GNOMONKIT_") or k == "CLAUDE_CONFIG_DIR":
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(H, "KEEP_DAYS", 0)        # the fixtures are dated; the age limit has its own test
    # memory.history is auto: a MemPalace on the developer's own PATH would turn indexing off. The auto
    # rule has its own tests (test_memory_backend.py), on a made-up PATH and config.
    monkeypatch.setattr(C, "mempalace_status", lambda cwd=None, refresh=False: dict(NO_MEMPALACE))
    C._CACHE.clear()
    yield tmp_path
    C._CACHE.clear()


# ---- synthetic transcripts -----------------------------------------------------------------------
def work(tmp_path, name="demo-app"):
    d = tmp_path / "work" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def tfile(tmp_path, session, folder="-work-demo-app", sub=None):
    base = tmp_path / "claude" / "projects" / folder
    if sub:
        p = base / session / "subagents" / ("agent-%s.jsonl" % sub)
    else:
        p = base / ("%s.jsonl" % session)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


_N = [0]


def rec(kind, text, session, cwd, day=1, **extra):
    _N[0] += 1
    uid = "%08x-0000-4000-8000-%012x" % (_N[0], _N[0])
    if kind == "user":
        msg = {"role": "user", "content": text}
    elif isinstance(text, list):
        msg = {"role": "assistant", "content": text}
    else:
        msg = {"role": "assistant", "content": [{"type": "text", "text": text}]}
    d = {"type": kind, "uuid": uid, "sessionId": session, "cwd": str(cwd),
         "timestamp": "2026-01-%02dT10:00:00.000Z" % day, "message": msg}
    d.update(extra)
    return json.dumps(d) + "\n"


def append(path, *lines, partial=""):
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write("".join(lines) + partial)


def chunks(where="", params=()):
    st = H.Store(readonly=True)
    try:
        return [dict(zip(("record", "role", "origin", "project", "session", "text"), r)) for r in st.con.execute(
            "SELECT record, role, origin, project, session, text FROM chunks " + where, params)]
    finally:
        st.close()


def search(text, **kw):
    return H.search(H.Query(text, **kw))


SID = "5e551011-aaaa-4bbb-8ccc-000000000001"
SID2 = "5e551022-aaaa-4bbb-8ccc-000000000002"


# ---- ingest --------------------------------------------------------------------------------------
def test_incremental_ingest_reads_only_appended_records(tmp_path):
    cwd = work(tmp_path)
    f = tfile(tmp_path, SID)
    append(f, rec("user", "set up the widget pipeline", SID, cwd), rec("assistant", "the widget pipeline is ready", SID, cwd))
    s = H.run_index()
    assert (s["records"], s["error"]) == (2, None)
    assert H.run_index()["records"] == 0                       # nothing new: nothing read
    half = rec("user", "now the gadget step", SID, cwd)
    append(f, rec("assistant", "added a retry to the widget", SID, cwd), partial=half[:30])
    assert H.run_index()["records"] == 1                       # the half-written line waits
    append(f, half[30:])
    assert H.run_index()["records"] == 1
    texts = [c["text"] for c in chunks()]
    assert len(texts) == 4 and len(set(texts)) == 4 and "now the gadget step" in texts


def test_what_is_indexed_and_what_is_not(tmp_path):
    cwd = work(tmp_path)
    f = tfile(tmp_path, SID)
    reminder = "<system-reminder>zebracorn reminder text</system-reminder>"
    note = "<task-notification>quokkafish finished</task-notification>"
    append(f,
           rec("user", "please fix the parser " + reminder + " " + note, SID, cwd),
           rec("user", "meta only marmotbadger", SID, cwd, isMeta=True),
           rec("user", "summary narwhalotter", SID, cwd, isCompactSummary=True),
           rec("assistant", [{"type": "text", "text": "running it"},
                             {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q tests/parser"}},
                             {"type": "tool_use", "name": "Read", "input": {"file_path": "/x/secret-plans.txt"}}],
               SID, cwd),
           json.dumps({"type": "user", "uuid": "tr-1", "sessionId": SID, "cwd": str(cwd),
                       "message": {"role": "user", "content": [
                           {"type": "tool_result", "tool_use_id": "t1", "content": "pelicanmoose output"}]}}) + "\n")
    append(tfile(tmp_path, SID, sub="a1"), rec("assistant", "subagent found the parser bug", SID, cwd))
    H.run_index()
    rows = chunks()
    text = " ".join(r["text"] for r in rows)
    assert "please fix the parser" in text and "[tool: Bash] pytest -q tests/parser" in text and "[tool: Read]" in text
    for gone in ("zebracorn", "quokkafish", "marmotbadger", "narwhalotter", "pelicanmoose", "secret-plans"):
        assert gone not in text, gone
    assert {r["origin"] for r in rows if "subagent found" in r["text"]} == {"subagent"}
    assert {r["project"] for r in rows} == {"demo-app"}
    assert {r["role"] for r in rows} == {"user", "assistant", "tool"}


def test_rebuild_reads_everything_again_without_doubling(tmp_path):
    cwd = work(tmp_path)
    append(tfile(tmp_path, SID), rec("user", "one widget", SID, cwd), rec("user", "two widgets", SID, cwd))
    H.run_index()
    assert H.run_index(rebuild=True)["records"] == 2
    assert len(chunks()) == 2


# ---- scrubbing -----------------------------------------------------------------------------------
def _secrets():
    """(kind, text holding a secret, the secret part) built from pieces, so no source line is one."""
    a, b = "a1B2", "Zx9q"
    return [
        ("private-key", "key:\n-----BEGIN " + "RSA PRIVATE KEY-----\nMIIE" + b * 12 + "\n-----END " + "RSA PRIVATE KEY-----",
         "MIIE" + b * 12),
        ("aws-access-key-id", "id " + "AKIA" + "QWERTYUIOPASDFGH" + " here", "AKIA" + "QWERTYUIOPASDFGH"),
        ("github-token", "use " + "ghp_" + a * 9, "ghp_" + a * 9),
        ("github-fine-grained-pat", "pat " + "github_pat_" + a * 6, "github_pat_" + a * 6),
        ("gitlab-token", "gl " + "glpat-" + b * 5, "glpat-" + b * 5),
        ("slack-token", "bot " + "xoxb-" + "1234-5678-" + b * 3, "xoxb-" + "1234-5678-" + b * 3),
        ("slack-webhook", "post to https://hooks." + "slack.com/services/" + "T0/B0/" + b * 6, b * 6),
        ("anthropic-key", "key " + "sk-ant-" + "api03-" + b * 6, "sk-ant-" + "api03-" + b * 6),
        ("openai-key", "key " + "sk-proj-" + b * 6, "sk-proj-" + b * 6),
        ("google-api-key", "maps " + "AIza" + "S" * 35, "AIza" + "S" * 35),
        ("stripe-key", "pay " + "sk_" + "live_" + b * 5, "sk_" + "live_" + b * 5),
        ("npm-token", "npm " + "npm_" + "q" * 36, "npm_" + "q" * 36),
        ("pypi-token", "upload " + "pypi-" + "AgEIcHlwaS5vcmc" + "Q" * 50, "AgEIcHlwaS5vcmc" + "Q" * 50),
        ("jwt", "jwt " + "eyJ" + "hbGciOiJIUzI1" + "." + "eyJ" + "zdWIiOiIxMjM0" + "." + "S" * 16, "S" * 16),
        ("authorization-header", "Authorization: " + "Basic " + "dXNlcjpw" + "YXNzd29y", "dXNlcjpw" + "YXNzd29y"),
        ("bearer-token", "header bearer " + "T0k3n" * 5, "T0k3n" * 5),
        ("url-credentials", "clone https://deploy:" + "s3cr3t" + "Pw9" + "@git.example.com/r.git", "s3cr3t" + "Pw9"),
        ("credential-assignment", "export DB_" + "PASSWORD=" + "hunter" + "22xq", "hunter" + "22xq"),
    ]


@pytest.mark.parametrize("kind,text,secret", _secrets(), ids=[k for k, _, _ in _secrets()])
def test_each_scrub_rule_catches_its_secret(kind, text, secret):
    out = S.Scrubber()(text)
    assert secret not in out
    assert "[REDACTED:%s]" % kind in out


def test_scrubber_keeps_shas_uuids_and_placeholders_in_running_text():
    sha = "3f" * 20
    uid = "123e4567-e89b-42d3-a456-426614174000"
    text = ("commit %s, session %s, short %s, digest sha256:%s, password=${DB_PASS}, api_key=<your key>, "
            "secret=changeme, token=$env:GH_TOKEN, max_tokens=100000" % (sha, uid, sha[:7], "3f" * 32))
    assert S.Scrubber()(text) == text


def _hex(n):
    return "9f3c" * (n // 4)


@pytest.mark.parametrize("text,kind", [
    ("JWT_" + "SECRET=" + _hex(64), "credential-assignment"),
    ("GITHUB_CLIENT_" + "SECRET=" + _hex(40), "credential-assignment"),
    ("token=" + _hex(40), "credential-assignment"),
    ("curl -H \"Authorization: " + "Bearer " + _hex(64) + "\" https://api.example.test", "authorization-header"),
    ("header bearer " + _hex(64), "bearer-token"),
    ("git clone https://deploy:" + _hex(40) + "@git.example.test/r.git", "url-credentials"),
])
def test_a_hex_value_after_a_secret_key_bearer_or_url_user_is_redacted(text, kind):
    out = S.Scrubber()(text)
    assert _hex(40) not in out
    assert "[REDACTED:%s]" % kind in out


def _pw():
    return "Sup3r" + "S3cret" + "Val9"


_CURL = "cu" + "rl"


@pytest.mark.parametrize("text,kind", [
    # escaped JSON inside a shell or PowerShell string
    ('curl -d "{\\"pass' + 'word\\":\\"' + _pw() + '\\"}" https://api.example.test', "credential-assignment"),
    ('Invoke-RestMethod -Body "{\\"api' + '_key\\":\\"' + _pw() + '\\"}"', "credential-assignment"),
    ('{\\"access' + '_token\\": \\"' + _pw() + '\\"}', "credential-assignment"),
    ('-Body "{`"api' + '_key`":`"' + _pw() + '`"}"', "credential-assignment"),
    ('{\\"Authorization\\": \\"' + 'Bearer ' + _pw() + '\\"}', "authorization-header"),
    # a quoted value with a space in it
    ("export DATABASE_" + "PASSWORD='" + _pw()[:5] + " " + _pw()[5:] + "'", "credential-assignment"),
    # basic auth on an HTTP client's command line (the client name in fragments, like the secrets)
    (_CURL + " -u admin:" + _pw() + " https://api.example.test", "basic-auth-flag"),
    (_CURL + ".exe --user 'admin:" + _pw() + "' https://api.example.test", "basic-auth-flag"),
    (_CURL + " -uadmin:" + _pw() + " https://api.example.test", "basic-auth-flag"),
    ("http -a admin:" + _pw() + " api.example.test", "basic-auth-flag"),
    # --password X and its relatives
    ("mysql --pass" + "word " + _pw() + " -h db", "password-flag"),
    ("wget --http-pass" + "word=" + _pw() + " https://files.example.test", "password-flag"),
    ("keytool -store" + "pass " + _pw() + " -list", "password-flag"),
    ("tool --api-key \"" + _pw() + "\"", "password-flag"),
    # -p<password> on database clients
    ("mysql -uroot -p" + _pw(), "db-client-password"),
    ("mysqldump -h db -u app -p'" + _pw() + "' shop", "db-client-password"),
    ("mariadb -u app -p" + _pw() + " shop", "db-client-password"),
    ("sshpass -p " + _pw() + " ssh deploy@host", "sshpass-password"),
    # a URL password holding a slash
    ("postgres://admin:" + _pw()[:6] + "/" + _pw()[6:] + "@db.example.test/x", "url-credentials"),
    # a number is a fine count but not a fine password
    ("pass" + "word=" + "73" + "9184" + "26", "credential-assignment"),
])
def test_each_credential_form_is_redacted(text, kind):
    out = S.Scrubber()(text)
    for piece in (_pw()[:5], _pw()[5:], "739184"):
        assert piece not in out, out
    assert "[REDACTED:%s]" % kind in out, out


@pytest.mark.parametrize("text", [
    "mkdir -p build/out && ssh -p 22 host",
    "docker run -p 8080:80 nginx",
    "mysql -u root -p shop",
    "mysql -h db -P 3306 -p",
    "git log -p --stat",
    "python -m pytest -q -p no:cacheprovider",
    "docker login --password-stdin < pw.txt",
    "gh auth login --with-token < token.txt",
    "curl -u alice https://api.example.test",
    "npm install https://registry.example.test:4873/@scope/pkg",
])
def test_ordinary_flags_are_left_alone(text):
    assert S.Scrubber()(text) == text


def test_url_credentials_keep_the_host():
    out = S.Scrubber()("https://deploy:" + "pa55" + "word9" + "@git.example.com/r.git")
    assert out == "https://deploy:[REDACTED:url-credentials]@git.example.com/r.git"


def test_a_record_the_scrubber_raises_on_is_dropped(tmp_path):
    cwd = work(tmp_path)
    append(tfile(tmp_path, SID), rec("user", "a fine widget note", SID, cwd), rec("user", "a poison widget note", SID, cwd))

    def scrub(t):
        if "poison" in t:
            raise RuntimeError("cannot check")
        return t
    s = H.run_index(scrubber=scrub)
    assert (s["records"], s["dropped"]) == (1, 1)
    assert "poison" not in json.dumps(chunks())


def test_broken_extra_rules_stop_the_index(tmp_path, monkeypatch):
    cwd = work(tmp_path)
    append(tfile(tmp_path, SID), rec("user", "ticket TICK-4821 is open", SID, cwd))
    rules = tmp_path / "rules.txt"
    rules.write_text("# extra rules\nticket\tTICK-(\n", encoding="utf-8")
    monkeypatch.setenv("GNOMONKIT_SCRUB_RULES", str(rules))
    with pytest.raises(S.ScrubConfigError):
        H.run_index()
    assert not os.path.exists(H.db_path()) or not chunks()
    rules.write_text("ticket\tTICK-\\d{4}\n", encoding="utf-8")
    H.run_index()
    text = chunks()[0]["text"]
    assert "TICK-4821" not in text and "[REDACTED:ticket]" in text
    assert "TICK-4821" not in json.dumps(search("ticket open")["results"])


def test_secrets_never_reach_the_index_or_the_output(tmp_path):
    cwd = work(tmp_path)
    tok = "ghp_" + "Q7r" * 12
    append(tfile(tmp_path, SID), rec("user", "deploy with token " + tok + " please", SID, cwd))
    H.run_index()
    with open(H.db_path(), "rb") as fh:
        assert tok.encode() not in fh.read()
    assert tok not in json.dumps(search("deploy token")["results"])


# ---- search --------------------------------------------------------------------------------------
def _corpus(tmp_path):
    a, b = work(tmp_path, "demo-app"), work(tmp_path, "other-app")
    append(tfile(tmp_path, SID),
           rec("user", "the toolchain cache broke again; pin the toolchain and clear the cache", SID, a, day=3),
           rec("user", "lunch plans for friday", SID, a, day=4))
    append(tfile(tmp_path, SID2, folder="-work-other-app"),
           rec("user", "the toolchain moved to a new version", SID2, b, day=5),
           rec("user", "cache sizes look fine", SID2, b, day=6))
    return a, b


@pytest.mark.skipif(not FTS, reason="this Python's SQLite has no FTS5")
def test_fts5_and_like_fallback_give_the_same_top_hit(tmp_path, monkeypatch):
    _corpus(tmp_path)
    fts = H.Store(path=str(tmp_path / "fts.db"), use_fts=True)
    like = H.Store(path=str(tmp_path / "like.db"), use_fts=False)
    try:
        assert fts.fts and not like.fts
        H.run_index(store=fts, lock=False)
        H.run_index(store=like, lock=False)
        q = H.Query("toolchain cache", cwd=str(tmp_path))
        r1, r2 = H.search(q, store=fts), H.search(q, store=like)
        assert (r1["mode"], r2["mode"]) == ("fts5", "like")
        assert r1["results"][0]["record"] == r2["results"][0]["record"]
        assert "pin the toolchain" in r1["results"][0]["text"]
        assert {r["record"] for r in r1["results"]} == {r["record"] for r in r2["results"]}
    finally:
        fts.close()
        like.close()


def test_like_fallback_is_used_and_said_when_fts5_is_missing(tmp_path, monkeypatch):
    _corpus(tmp_path)
    monkeypatch.setattr(H, "_FTS5", False)
    s = H.run_index()
    assert s["mode"] == "like"
    res = search("toolchain cache")
    assert res["mode"] == "like" and "pin the toolchain" in res["results"][0]["text"]
    assert H.status()["mode"] == "like"


def test_repo_bonus_reorders_without_dropping(tmp_path):
    a, b = _corpus(tmp_path)
    H.run_index()
    plain = search("toolchain", cwd=str(tmp_path))
    here_b = search("toolchain", cwd=str(b))
    assert {r["record"] for r in plain["results"]} == {r["record"] for r in here_b["results"]}
    assert len(here_b["results"]) == 2
    assert here_b["results"][0]["project"] == "other-app" and here_b["results"][0].get("here") is True
    here_a = search("toolchain", cwd=str(a))
    assert here_a["results"][0]["project"] == "demo-app"


def test_project_and_since_filters(tmp_path):
    a, b = _corpus(tmp_path)
    H.run_index()
    assert {r["project"] for r in search("toolchain cache", project="other-app")["results"]} == {"other-app"}
    assert {r["project"] for r in search("toolchain cache", project="here", cwd=str(a))["results"]} == {"demo-app"}
    assert [r["ts"][:10] for r in search("cache", since="2026-01-06")["results"]] == ["2026-01-06"]
    with pytest.raises(ValueError):
        H.parse_since("last tuesday")


def test_the_query_log_never_holds_the_words(tmp_path):
    _corpus(tmp_path)
    H.run_index()
    res = search("toolchain cache")
    with open(os.path.join(H.history_dir(), "queries.jsonl"), encoding="utf-8") as fh:
        log = fh.read()
    assert res["qid"] in log and "toolchain" not in log


def test_mark_validates_the_id_and_rank(tmp_path):
    _corpus(tmp_path)
    H.run_index()
    res = search("toolchain")
    with pytest.raises(ValueError):
        H.mark("not-an-id", 1)
    with pytest.raises(ValueError):
        H.mark("q0123456789", 1)                               # well formed, never issued
    with pytest.raises(ValueError):
        H.mark(res["qid"], len(res["results"]) + 1)
    assert H.mark(res["qid"], 1)["used"] is True
    assert H.mark(res["qid"], None)["used"] is False
    with open(os.path.join(H.history_dir(), "marks.jsonl"), encoding="utf-8") as fh:
        assert len(fh.readlines()) == 2


# ---- a new source plugs in without changing the core loop ----------------------------------------
def test_a_new_source_is_a_module_with_records(tmp_path, monkeypatch):
    import types
    state = {"text": "the export button fails on empty tables"}

    def records(ctx):
        assert ctx.cursor("since") in (None, "2026-01-02T00:00:00Z")
        yield {"id": "issue-12", "text": state["text"], "timestamp": "2026-01-02T09:00:00Z", "origin": "work",
               "project": "demo-app", "ref": "owner/demo-app#12", "url": "https://example.com/owner/demo-app/issues/12"}
        ctx.set_cursor("since", "2026-01-02T00:00:00Z")
    mod = types.SimpleNamespace(NAME="work", records=records, enabled=lambda args: True)
    monkeypatch.setattr(H, "source_modules", lambda: [mod])
    H.run_index()
    state["text"] = "the export button fails on empty tables and on huge ones"
    H.run_index()                                             # re-yielded: replaced, not doubled
    rows = chunks()
    assert len(rows) == 1 and "huge ones" in rows[0]["text"] and rows[0]["origin"] == "work"
    top = search("export button")["results"][0]
    assert (top["ref"], top["url"]) == ("owner/demo-app#12", "https://example.com/owner/demo-app/issues/12")


def test_the_shipped_sources_are_discovered():
    assert [m.NAME for m in H.source_modules()] == ["sessions", "work"]


# ---- exclusions and forget -----------------------------------------------------------------------
def test_excluded_projects_and_the_marker_file(tmp_path):
    a, b = _corpus(tmp_path)
    s = H.run_index(exclusions=H.Exclusions(["other-app"]))
    assert s["excluded"] == 2
    assert {r["project"] for r in chunks()} == {"demo-app"}
    (a / H.Exclusions.MARKER).write_text("", encoding="utf-8")
    s = H.run_index(exclusions=H.Exclusions([]))
    assert s["purged"] == 2 and chunks() == []                # already-indexed rows of a marked repo go too


def test_excluded_by_folder_and_by_the_profile(tmp_path):
    a, b = _corpus(tmp_path)
    H.run_index(exclusions=H.Exclusions([str(b)]))
    assert {r["project"] for r in chunks()} == {"demo-app"}
    assert C.check_value(C.SCHEMA_BY_KEY["memory.exclude_projects"], ["demo-app", str(b)]) is None
    prof = tmp_path / "personal.toml"
    prof.write_text('[memory]\nexclude_projects = ["demo-app"]\n', encoding="utf-8")
    os.environ["GNOMONKIT_PERSONAL_PROFILE"] = str(prof)
    try:
        C._CACHE.clear()
        assert H.Exclusions()("demo-app", "")
    finally:
        del os.environ["GNOMONKIT_PERSONAL_PROFILE"]


def test_forget_session_project_and_all(tmp_path):
    _corpus(tmp_path)
    H.run_index()
    assert H.forget(session=SID[:4]) == 0                     # a short prefix names nothing
    assert H.forget(session=SID[:8]) == 2
    assert {r["session"] for r in chunks()} == {SID2}
    assert H.forget(project="OTHER-APP") == 2
    assert chunks() == []
    H.run_index(rebuild=True)
    assert H.forget(everything=True) == 4 and chunks() == []
    with pytest.raises(ValueError):
        H.forget()


# ---- the run: lock, refresh timing, discovery file -----------------------------------------------
def test_lock_keeps_one_run_at_a_time(tmp_path):
    lk = H.Lock().acquire()
    try:
        with pytest.raises(H.Busy):
            H.run_index()
        assert H.due() is False
    finally:
        lk.release()
    old = time.time() - H.LOCK_STALE - 60
    H.Lock().acquire()                                        # left behind by a run that died
    os.utime(os.path.join(H.history_dir(), "index.lock"), (old, old))
    assert H.run_index()["error"] is None


def test_due_after_fifteen_minutes(tmp_path, monkeypatch):
    assert H.due() is True
    H.run_index()
    assert H.due() is False
    assert H.due(now=time.time() + H.REFRESH_EVERY + 1) is True
    monkeypatch.setenv("GNOMONKIT_HISTORY", "off")
    assert H.due(now=time.time() + H.REFRESH_EVERY + 1) is False
    assert H.run_index() == {"disabled": True}


def test_search_json_and_file_modes(tmp_path):
    H.run_index()
    with open(os.path.join(H.history_dir(), "search.json"), encoding="utf-8") as fh:
        d = json.load(fh)
    assert d["argv"][-1] == "--" and d["argv"][2:4] == ["history", "search"]
    assert float(d["argv"][d["argv"].index("--budget") + 1]) == H.SEARCH_BUDGET_S   # hooks never wait long
    assert os.path.isfile(d["argv"][1]) and d["db"] == H.db_path()
    if os.name != "nt":
        assert os.stat(H.db_path()).st_mode & 0o777 == 0o600
        assert os.stat(H.history_dir()).st_mode & 0o777 == 0o700


def test_chunking_overlaps_and_cuts_at_spaces():
    text = " ".join("word%03d" % i for i in range(400))
    parts = H.chunk_text(text)
    assert len(parts) > 2 and all(len(p) <= H.CHUNK for p in parts)
    assert all(p.split()[0].startswith("word") for p in parts)       # never mid-word
    assert parts[1].split()[0] in parts[0]                          # the next one starts inside the last


# ---- the command ---------------------------------------------------------------------------------
def cli(capsys, *argv):
    rc = gnomonkit.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def test_cli_index_status_search_mark_forget(tmp_path, capsys):
    _corpus(tmp_path)
    rc, out, _ = cli(capsys, "history", "search", "toolchain")
    assert rc == 0 and out == ""                              # no index yet: said on stderr
    rc, out, _ = cli(capsys, "history", "index")
    assert rc == 0 and "Indexed 4 new records" in out
    rc, out, _ = cli(capsys, "history", "status", "--json")
    st = json.loads(out)
    assert rc == 0 and st["chunks"] == 4 and st["sessions"] == 2 and st["search_json"] is True
    rc, out, err = cli(capsys, "history", "search", "zzzunmatched")
    assert rc == 0 and out == "" and "nothing found" in err   # stdout empty: "nothing" to a caller
    rc, out, _ = cli(capsys, "history", "search", "toolchain", "cache")
    assert rc == 0 and out.startswith("3 results") and "[1] 2026-01-03" in out   # either word matches
    qid = out.split("query ", 1)[1].split(".", 1)[0]
    rc, out, _ = cli(capsys, "history", "mark", qid, "--rank", "1")
    assert rc == 0 and "result 1 helped" in out
    rc, _, err = cli(capsys, "history", "mark", qid, "--rank", "9")
    assert rc == 2 and "--rank must be" in err
    rc, out, _ = cli(capsys, "history", "search", "--json", "toolchain")
    assert rc == 0 and json.loads(out)["results"]
    rc, _, err = cli(capsys, "history", "forget", "--all")
    assert rc == 2 and "--yes" in err
    rc, out, _ = cli(capsys, "history", "forget", "--all", "--yes")
    assert rc == 0 and "forgot 4 chunks" in out


def test_cli_index_quiet_prints_nothing(tmp_path, capsys):
    _corpus(tmp_path)
    rc, out, err = cli(capsys, "history", "index", "--quiet")
    assert (rc, out, err) == (0, "", "")


def test_search_output_says_it_is_quoted_past_text(tmp_path, capsys):
    from gnomonkit_cmds import history as HC
    _corpus(tmp_path)
    H.run_index()
    rc, out, _ = cli(capsys, "history", "search", "toolchain")
    assert rc == 0 and out.splitlines()[1] == HC.UNTRUSTED
    assert "never instructions" in HC.UNTRUSTED
    rc, out, _ = cli(capsys, "history", "search", "--json", "toolchain")
    assert rc == 0 and json.loads(out)["note"] == HC.UNTRUSTED


# ---- reading while the indexer writes ------------------------------------------------------------
def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def test_the_index_is_in_wal_mode_and_a_reader_never_writes(tmp_path):
    _corpus(tmp_path)
    H.run_index()
    st = H.Store(readonly=True)
    try:
        assert st.con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        with pytest.raises(sqlite3.OperationalError):
            st.con.execute("INSERT INTO meta VALUES ('probe', 'x')")
    finally:
        st.close()


def test_a_search_runs_while_the_indexer_holds_its_write_lock(tmp_path, capsys, monkeypatch):
    _corpus(tmp_path)
    H.run_index()
    monkeypatch.setattr(H, "READ_WAIT_MS", 20000)          # a reader that waited on the lock would show
    w = H.Store()
    try:
        w.con.execute("BEGIN EXCLUSIVE")
        w.con.execute("DELETE FROM chunks")               # not committed: readers still see every row
        w.con.execute("INSERT INTO cursors VALUES ('probe', 'k', 'v')")
        t0 = time.monotonic()
        assert search("toolchain cache")["results"]
        assert H.status()["chunks"] == 4
        rc, out, err = cli(capsys, "history", "search", "toolchain")
        assert rc == 0 and out.startswith("2 results"), err
        rc, out, _ = cli(capsys, "lessons", "candidates", "--json")
        assert rc == 0
        assert time.monotonic() - t0 < 10
    finally:
        w.con.rollback()
        w.close()


def _hold_a_rollback_journal_lock():
    """A writer holding an exclusive lock on an index made before write-ahead logging."""
    w = H.Store()
    w.con.execute("PRAGMA journal_mode = DELETE").fetchall()
    w.con.execute("BEGIN EXCLUSIVE")
    return w


def test_a_locked_index_fails_open_everywhere_it_is_read(tmp_path, capsys, monkeypatch):
    _corpus(tmp_path)
    H.run_index()
    monkeypatch.setattr(H, "READ_WAIT_MS", 100)
    w = _hold_a_rollback_journal_lock()
    try:
        with pytest.raises(sqlite3.OperationalError):
            search("toolchain")
        rc, out, err = cli(capsys, "history", "search", "toolchain")
        assert (rc, out) == (0, "") and len(err.strip().splitlines()) == 1 and "can't be read just now" in err
        assert "error" in H.status()
        rc, _, err = cli(capsys, "lessons", "candidates")
        assert rc == 2 and "can't" in err
    finally:
        w.con.rollback()
        w.close()


def test_the_interjector_stays_silent_on_a_locked_index(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(os.path.join(ROOT, "plugins", "interjector", "hooks"))
    monkeypatch.setenv("GNOMONKIT_PERSONAL_PROFILE", str(tmp_path / "nowhere" / "profile.toml"))
    import ij_history
    _corpus(tmp_path)
    H.run_index()
    assert "toolchain" in (ij_history.search("toolchain cache") or "")       # through search.json
    w = _hold_a_rollback_journal_lock()
    try:
        t0 = time.monotonic()
        assert ij_history.search("toolchain cache") == ""
        assert time.monotonic() - t0 < ij_history.CORE_TIMEOUT_S
    finally:
        w.con.rollback()
        w.close()


def test_a_search_past_its_budget_stops_and_says_so(tmp_path, capsys, monkeypatch):
    _corpus(tmp_path)
    H.run_index()
    ticks = iter([0.0] + [100.0] * 100000)                 # the deadline, then a clock long past it
    monkeypatch.setattr(H, "_clock", lambda: next(ticks))
    monkeypatch.setattr(H, "PROGRESS_EVERY", 1)
    with pytest.raises(H.SearchTimeout):
        H.search(H.Query("toolchain cache"), budget=2)
    ticks = iter([0.0] + [100.0] * 100000)
    rc, out, err = cli(capsys, "history", "search", "--budget", "2", "toolchain")
    assert (rc, out) == (0, "") and "budget" in err and len(err.strip().splitlines()) == 1
    assert search("toolchain")["results"]                  # no budget: the same search runs to the end


@pytest.mark.skipif(not FTS, reason="this Python's SQLite has no FTS5")
def test_a_reader_never_builds_the_full_text_index(tmp_path):
    _corpus(tmp_path)
    like = H.Store(use_fts=False)                          # as a Python without FTS5 made it
    try:
        H.run_index(store=like)
    finally:
        like.close()
    before = _sha(H.db_path())
    res = search("toolchain cache")
    assert res["mode"] == "like" and res["results"]
    assert H.status()["mode"] == "like"
    assert _sha(H.db_path()) == before                     # not a byte written, the schema included
    con = sqlite3.connect(H.db_path())
    try:
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name = 'chunks_fts'").fetchone() is None
    finally:
        con.close()
    H.run_index()                                          # the indexer builds it, over the rows already there
    res = search("toolchain cache")
    assert res["mode"] == "fts5" and "pin the toolchain" in res["results"][0]["text"]


# ---- retention -----------------------------------------------------------------------------------
def test_old_rows_are_pruned_by_age(tmp_path):
    cwd = work(tmp_path)
    append(tfile(tmp_path, SID), rec("user", "an old widget note", SID, cwd, day=2),
           rec("user", "a fresh widget note", SID, cwd, timestamp=H.now_iso()))
    st = H.Store()
    try:
        H.run_index(store=st)
        assert H.prune(st, keep_days=30, max_mb=0) == 1
    finally:
        st.close()
    assert [c["text"] for c in chunks()] == ["a fresh widget note"]


def test_the_profile_sets_the_limits_and_the_run_applies_them(tmp_path, monkeypatch):
    cwd = work(tmp_path)
    append(tfile(tmp_path, SID), rec("user", "an old gadget note", SID, cwd, day=2),
           rec("user", "a fresh gadget note", SID, cwd, timestamp=H.now_iso()))
    assert H.retention() == (0, H.MAX_MB)                  # the fixture turned the age limit off
    prof = tmp_path / "personal.toml"
    prof.write_text("[memory]\nhistory_keep_days = 30\nhistory_max_mb = 0\n", encoding="utf-8")
    monkeypatch.setenv("GNOMONKIT_PERSONAL_PROFILE", str(prof))
    C._CACHE.clear()
    assert H.retention() == (30, 0)
    s = H.run_index()
    assert s["pruned"] == 1 and [c["text"] for c in chunks()] == ["a fresh gadget note"]
    assert H.status()["keep_days"] == 30


def test_past_the_size_cap_the_oldest_days_go_first(tmp_path):
    st = H.Store()
    try:
        for day in range(1, 29):
            for k in range(40):
                r = {"id": "r%02d-%02d" % (day, k), "origin": "transcript", "ts": "2026-02-%02dT10:00:00Z" % day}
                st.replace_record("t", r, ["day %d note %d %s" % (day, k, "filler " * 140)])
        st.commit()
        assert H._used_bytes(st) > 1024 * 1024
        n = H.prune(st, keep_days=0, max_mb=1)
        days = sorted({r[0] for r in st.con.execute("SELECT substr(ts, 1, 10) FROM chunks")})
    finally:
        st.close()
    assert n > 0 and days and days[-1] == "2026-02-28"       # the newest stay
    assert "2026-02-01" not in days                          # the oldest went first
    assert days == ["2026-02-%02d" % d for d in range(int(days[0][-2:]), 29)]   # no gaps: whole days, oldest first


# ---- long records --------------------------------------------------------------------------------
def test_a_long_record_is_scrubbed_before_it_is_cut(tmp_path):
    cwd = work(tmp_path)
    tok = "ghp_" + "Q7r" * 12
    lead = "word " * ((H.RECORD_MAX - 12) // 5)            # the token starts just before the cut
    assert len(lead) < H.RECORD_MAX < len(lead) + len(tok)
    append(tfile(tmp_path, SID), rec("user", lead + tok + " and more words after it", SID, cwd))
    H.run_index()
    body = "".join(c["text"] for c in chunks())
    assert "Q7rQ7r" not in body and "ghp_" not in body
    with open(H.db_path(), "rb") as fh:
        assert b"Q7rQ7r" not in fh.read()


def test_a_huge_record_reaches_the_scrubber_cut_at_a_whole_word():
    r = H.normalise_record({"id": "r1", "text": "head " + "b" * H.RAW_MAX}, "t")
    assert r["text"] == "head "                             # half a run would no longer look like a token
    r = H.normalise_record({"id": "r2", "text": "word " * (H.RAW_MAX // 5 + 10)}, "t")
    assert len(r["text"]) <= H.RAW_MAX and r["text"].endswith(" ")
    assert H.normalise_record({"id": "r3", "text": "short"}, "t")["text"] == "short"
