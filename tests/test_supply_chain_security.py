"""Regression tests for the PR #38 security review, one block per finding, in the review's order.

Every probe is synthetic: hostile archives are assembled byte by byte in memory, upstream
histories are throwaway git repos with made-up authors, and HTTP goes through a fake opener.
"""
import io
import json
import os
import tarfile
import urllib.error
import urllib.request
import zipfile

import pytest

import sc_fixtures as fx

from supply_chain import archive, history, llm, netfetch, patch, review, text

CLEAN_OLD = {"index.js": "module.exports = 1\n"}
CLEAN_NEW = {"index.js": "module.exports = 2\n"}


def _rules(arc_or_rep):
    if isinstance(arc_or_rep, archive.Archive):
        return {(s, r) for s, r, _p, _w in arc_or_rep.findings}
    return {(f.severity, f.rule) for f in arc_or_rep.flags}


def _tar_raw(files: dict, prefix="package/") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for path, body in files.items():
            data = body.encode()
            ti = tarfile.TarInfo(prefix + path)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _members_only(raw: bytes) -> bytes:
    """A tar stream without its end-of-archive zero blocks."""
    return raw.rstrip(b"\x00")


def _pad(b: bytes) -> bytes:
    return b + b"\x00" * (-len(b) % 512)


def _gz(raw: bytes) -> bytes:
    import gzip
    return gzip.compress(raw)


# ----------------------------------------------------------------------------- 1. tar parser differential
def test_entry_hidden_behind_one_zero_block_is_flagged():
    first = _pad(_members_only(_tar_raw({"index.js": "ok\n"})))
    hidden = _tar_raw({"evil.js": "require('child_process').exec('id')\n"})
    blob = _gz(first + b"\x00" * 512 + hidden)
    arc = archive.read(blob, "p.tgz")
    assert "evil.js" not in arc.files                       # Python's reader stops at the zero block...
    got = _rules(arc)
    assert ("high", "archive-trailing-data") in got        # ...and the rest of the stream is not zeros
    assert ("high", "archive-hidden-entry") in got
    assert any(p == "package/evil.js" for _s, r, p, _w in arc.findings if r == "archive-hidden-entry")


def test_entry_after_a_bad_checksum_header_is_flagged():
    raw = bytearray(_members_only(_tar_raw({"index.js": "ok\n", "junk.js": "x\n", "evil.js": "e\n"})))
    second = 1024                                           # header of junk.js: break its checksum
    raw[second + 148:second + 156] = b"0000000\x00"
    arc = archive.read(_gz(bytes(raw) + b"\x00" * 1024), "p.tgz")
    assert ("high", "archive-trailing-data") in _rules(arc)
    assert ("high", "archive-hidden-entry") in _rules(arc)


def test_bad_first_header_is_a_high_flag_not_not_checked():
    raw = bytearray(_tar_raw({"index.js": "ok\n"}))
    raw[148:156] = b"0000000\x00"
    web = fx.FakeWeb()
    fx.npm_package(web, "pad-thing", {"1.0.0": {"files": CLEAN_OLD}, "1.1.0": {"files": CLEAN_NEW}})
    url = "https://registry.npmjs.org/pad-thing/-/pad-thing-1.1.0.tgz"
    bad = _gz(bytes(raw))
    web.add(url, bad)
    doc = json.loads(web.routes["https://registry.npmjs.org/pad-thing"][1])
    doc["versions"]["1.1.0"]["dist"]["integrity"] = fx.sri(bad)
    web.add("https://registry.npmjs.org/pad-thing", doc)
    rep = fx.engine(web).review("npm", "pad-thing", "1.1.0", "1.0.0", review.Options(committers="off"))
    assert ("high", "unreviewable-artifact") in _rules(rep)


def test_clean_npm_style_tarball_has_no_differential_findings():
    """A normal archive (end blocks plus record padding) must stay quiet: calibration, not just recall."""
    arc = archive.read(fx.tgz({"index.js": "ok\n", "lib/a.js": "a\n"}), "p.tgz")
    assert arc.findings == [] and set(arc.files) == {"index.js", "lib/a.js"}


# ----------------------------------------------------------------------------- 2. new-committer bypasses
@pytest.fixture
def upstream(tmp_path):
    repo = fx.init_repo(tmp_path / "up")
    fx.commit_as(repo, "maintainer-one", {"index.js": "1\n"}, "init")
    fx.git(repo, "tag", "v1.0.0")
    return repo


def _check(repo, files_new=None):
    fx.git(repo, "tag", "v1.1.0")
    web = fx.FakeWeb()
    fx.npm_package(web, "pad-thing", {"1.0.0": {"files": CLEAN_OLD}, "1.1.0": {"files": files_new or CLEAN_NEW}})
    return fx.engine(web).review("npm", "pad-thing", "1.1.0", "1.0.0", review.Options(committers=str(repo)))


def test_pipe_in_email_does_not_match_another_authors_history(upstream):
    # As a regex, "x|maintainer-one" would match the maintainer's earlier commit.
    fx.commit_as(upstream, "sneaky", {"index.js": "2\n"}, "x", email="x|maintainer-one@example.com")
    rep = _check(upstream)
    assert [a.author for a in rep.committers.new_authors] == ["sneaky"]


def test_name_that_says_bot_is_still_checked(upstream):
    fx.commit_as(upstream, "renovate[bot]", {"index.js": "2\n"}, "x", email="impostor@example.com")
    rep = _check(upstream)
    assert [a.author for a in rep.committers.new_authors] == ["renovate[bot]"]
    assert ("medium", "new-committer") in _rules(rep)


def test_real_bot_noreply_identity_is_excluded(upstream):
    fx.commit_as(upstream, "dependabot[bot]", {"index.js": "2\n"}, "x", email=fx.BOT_EMAIL)
    rep = _check(upstream)
    assert rep.committers.authors == 0 and rep.committers.new_authors == []


def test_empty_email_counts_as_new_and_unchecked(upstream):
    fx.commit_as(upstream, "no-address", {"index.js": "2\n"}, "x", email="")
    rep = _check(upstream)
    assert [a.author for a in rep.committers.new_authors] == ["no-address"]
    assert any("no checkable identity" in u for u in rep.unchecked)


# ----------------------------------------------------------------------------- 3. repository from the old version
def test_repository_comes_from_the_old_version_and_a_move_is_flagged():
    web = fx.FakeWeb()
    fx.npm_package(web, "pad-thing", {"1.0.0": {"files": CLEAN_OLD}, "1.1.0": {"files": CLEAN_NEW}})
    doc = json.loads(web.routes["https://registry.npmjs.org/pad-thing"][1])
    doc["versions"]["1.0.0"]["repository"] = {"url": "git+https://github.com/real-org/pad-thing.git"}
    doc["versions"]["1.1.0"]["repository"] = {"url": "git+https://github.com/clean-fork/pad-thing.git"}
    web.add("https://registry.npmjs.org/pad-thing", doc)
    rep = fx.engine(web).review("npm", "pad-thing", "1.1.0", "1.0.0")
    assert ("medium", "repository-changed") in _rules(rep)
    gh = [u for u, _h in web.requests if u.startswith("https://api.github.com/")]
    assert gh and all("/repos/real-org/pad-thing/" in u for u in gh)


# ----------------------------------------------------------------------------- 4. commit pagination and caps
def test_compare_is_paginated_and_a_short_read_is_unchecked(monkeypatch):
    monkeypatch.setattr(history, "PAGE", 2)
    monkeypatch.setattr(history, "MAX_COMMITS", 4)
    web = fx.FakeWeb()
    api = "https://api.github.com/repos/o/r"

    def c(i):
        return {"sha": "%040d" % i, "parents": [{}], "author": {"login": "dev%d" % i},
                "commit": {"author": {"name": "d", "email": "d%d@example.com" % i}}}
    web.add(api + "/compare/a...b?per_page=2&page=1", {"total_commits": 7, "commits": [c(1), c(2)]})
    web.add(api + "/compare/a...b?per_page=2&page=2", {"total_commits": 7, "commits": [c(3), c(4)]})
    src = history.GitHubSource("o/r", fx.http(web))
    commits, total = src.commits("a", "b")
    assert [x.login for x in commits] == ["dev1", "dev2", "dev3", "dev4"] and total == 7


def test_author_cap_goes_to_unchecked(upstream, monkeypatch):
    monkeypatch.setattr(history, "MAX_AUTHOR_CHECKS", 1)
    fx.commit_as(upstream, "a-one", {"a.js": "1\n"}, "x")
    fx.commit_as(upstream, "a-two", {"b.js": "1\n"}, "x")
    rep = _check(upstream)
    assert any("authors were not checked" in u for u in rep.unchecked)


# ----------------------------------------------------------------------------- 5. unreviewable means not clean
def test_failed_download_is_an_error_and_exit_2(monkeypatch):
    from supply_chain import __main__ as cli
    web = fx.FakeWeb()
    fx.npm_package(web, "pad-thing", {"1.0.0": {"files": CLEAN_OLD}, "1.1.0": {"files": CLEAN_NEW}})
    del web.routes["https://registry.npmjs.org/pad-thing/-/pad-thing-1.1.0.tgz"]
    rep = fx.engine(web).review("npm", "pad-thing", "1.1.0", "1.0.0", review.Options(committers="off"))
    assert rep.error and "download failed" in rep.error
    monkeypatch.setattr(cli, "_http", lambda args: fx.http(web))
    assert cli.main(["release", "npm", "pad-thing", "1.0.0..1.1.0", "--committers", "off", "--no-model"]) == 2


def test_unreadable_archive_is_high():
    web = fx.FakeWeb()
    fx.npm_package(web, "pad-thing", {"1.0.0": {"files": CLEAN_OLD}, "1.1.0": {"files": CLEAN_NEW}})
    junk = b"\x1f\x8b" + b"not really gzip" * 10
    web.add("https://registry.npmjs.org/pad-thing/-/pad-thing-1.1.0.tgz", junk)
    doc = json.loads(web.routes["https://registry.npmjs.org/pad-thing"][1])
    doc["versions"]["1.1.0"]["dist"]["integrity"] = fx.sri(junk)
    web.add("https://registry.npmjs.org/pad-thing", doc)
    rep = fx.engine(web).review("npm", "pad-thing", "1.1.0", "1.0.0", review.Options(committers="off"))
    assert ("high", "unreviewable-artifact") in _rules(rep)


# ----------------------------------------------------------------------------- 6. memory bounds
def test_decompression_past_the_cap_is_refused(monkeypatch):
    monkeypatch.setattr(archive, "MAX_TOTAL", 64 * 1024)
    bomb = _gz(_tar_raw({"zeros.js": "0" * (256 * 1024)}))
    assert len(bomb) < 4096
    with pytest.raises(archive.ArchiveError):
        archive.read(bomb, "p.tgz")


def test_oversize_pax_header_is_refused_before_tarfile_reads_it(monkeypatch):
    monkeypatch.setattr(archive, "MAX_EXT_HEADER", 1024)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        ti = tarfile.TarInfo("package/a.js")
        ti.pax_headers = {"comment": "x" * 4096}
        ti.size = 1
        tf.addfile(ti, io.BytesIO(b"1"))
    with pytest.raises(archive.ArchiveError):
        archive.read(_gz(buf.getvalue()), "p.tgz")


# ----------------------------------------------------------------------------- 7. crashes
def test_encrypted_zip_member_is_a_finding_not_a_crash():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pkg/a.py", "x = 1\n")
        zf.writestr("pkg/secret.py", "x = 2\n")
    data = bytearray(buf.getvalue())
    # Mark secret.py encrypted in the central directory (zipfile's writer would clear the bit).
    cd = data.find(b"PK\x01\x02", data.find(b"PK\x01\x02") + 4)
    data[cd + 8] |= 0x1
    with pytest.raises(RuntimeError):                       # what an unguarded reader does
        zipfile.ZipFile(io.BytesIO(bytes(data))).read("pkg/secret.py")
    arc = archive.read(bytes(data), "p.whl")
    assert ("high", "encrypted-member") in _rules(arc) and "pkg/a.py" in arc.files


def test_patch_mode_survives_one_crashing_package(tmp_path, monkeypatch):
    repo = fx.init_repo(tmp_path / "app")
    fx.commit_as(repo, "dev", {"requirements.txt": "aaa-lib==1.0\nbbb-lib==1.0\n"}, "init")
    (repo / "requirements.txt").write_text("aaa-lib==1.1\nbbb-lib==1.1\n")
    eng = fx.engine(fx.FakeWeb())
    real = eng.review

    def flaky(eco, name, *a, **k):
        if name == "aaa-lib":
            raise RuntimeError("boom")
        return real(eco, name, *a, **k)
    monkeypatch.setattr(eng, "review", flaky)
    res = patch.run(eng, str(repo), opt=review.Options(committers="off"))
    by = {r.name: r for r in res.reports}
    assert set(by) == {"aaa-lib", "bbb-lib"} and "crashed" in by["aaa-lib"].error


def test_extract_skips_unportable_names_and_clashes(tmp_path):
    arc = archive.Archive()
    for p in ("ok.js", "x/Q:evil.txt", "trail.", "space ", "con.js", "aux", "a", "a/b"):
        arc.files[p] = archive.Member(p, 1, "", b"1")
    n, skipped = archive.extract_to(arc, str(tmp_path / "out"))
    why = dict(skipped)
    assert n == 2 and (tmp_path / "out" / "ok.js").exists() and (tmp_path / "out" / "a").is_file()
    assert {"x/Q:evil.txt", "trail.", "space ", "con.js", "aux", "a/b"} == set(why)
    assert "clash" in why["a/b"] or "could not be written" in why["a/b"]


# ----------------------------------------------------------------------------- 8. token and redirects
class _Req(urllib.request.Request):
    pass


def _redirect(src_url, dst_url, headers):
    h = netfetch.SafeRedirect()
    req = urllib.request.Request(src_url, headers=headers)
    return h.redirect_request(req, io.BytesIO(b""), 302, "Found", {}, dst_url)


def test_redirect_off_https_is_refused():
    with pytest.raises(urllib.error.HTTPError):
        _redirect("https://registry.npmjs.org/x", "http://example.com/x", {})


def test_github_api_redirect_is_refused():
    with pytest.raises(urllib.error.HTTPError):
        _redirect("https://api.github.com/repos/o/r", "https://api.github.com/repositories/1", {"Authorization": "Bearer t"})


def test_authorization_dropped_on_host_change():
    r = _redirect("https://pub.dev/api/archives/x.tar.gz", "https://storage.example.com/x.tar.gz",
                  {"Authorization": "Bearer t"})
    assert r is not None and not any(k.lower() == "authorization" for k in list(r.headers) + list(r.unredirected_hdrs))


def test_moved_repository_is_followed_only_to_the_same_host_id_path():
    web = fx.FakeWeb()
    old = "https://api.github.com/repos/old-owner/thing"
    moved = "https://api.github.com/repositories/4242"
    web.add(old + "/commits/1.0.0", b"", status=301, location=moved + "/commits/1.0.0")
    web.add(moved + "/commits/1.0.0", "a" * 40)
    src = history.GitHubSource("old-owner/thing", fx.http(web, token="t" * 12))
    assert src.resolve("thing", "1.0.0") == ("a" * 40, "1.0.0")
    assert src.base == moved and src.moved
    sent = [u for u, h in web.requests if any(v == "Bearer " + "t" * 12 for v in h.values())]
    assert all(u.startswith("https://api.github.com/") for u in sent)


def test_redirect_to_another_place_is_not_followed():
    web = fx.FakeWeb()
    base = "https://api.github.com/repos/o/r"
    for c in history.tag_candidates("r", "1.0.0"):
        web.add(base + "/commits/" + c, b"", status=302, location="https://attacker.example/steal")
    src = history.GitHubSource("o/r", fx.http(web))
    with pytest.raises(history.SourceError):
        src.resolve("r", "1.0.0")
    assert not any(u.startswith("https://attacker.example") for u, _h in web.requests)


def test_default_opener_uses_the_safe_redirect_handler():
    assert any(isinstance(h, netfetch.SafeRedirect) for h in netfetch._OPENER.handlers)


def test_download_time_budget(monkeypatch):
    class Slow:
        status = 200

        def __init__(self):
            self.n = 0

        def read(self, n=-1):
            self.n += 1
            return b"x" if self.n < 1000 else b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    clock = iter(range(0, 10_000, 10))
    monkeypatch.setattr(netfetch.time, "monotonic", lambda: next(clock))
    h = netfetch.HTTP(opener=lambda req, timeout=None: Slow(), budget=50)
    with pytest.raises(netfetch.FetchError):
        h.get("https://registry.npmjs.org/slow")


# ----------------------------------------------------------------------------- 9. local model transport
def test_model_opener_ignores_proxies_and_redirects(monkeypatch):
    import importlib
    monkeypatch.setenv("HTTP_PROXY", "http://198.51.100.9:3128")
    fresh = importlib.reload(llm)
    try:
        # A default opener would route 127.0.0.1 through that proxy; this one has no proxy handler at all.
        assert not any(isinstance(h, urllib.request.ProxyHandler) and h.proxies for h in fresh._OPENER.handlers)
        assert urllib.request.build_opener().handlers and any(
            isinstance(h, urllib.request.ProxyHandler) and h.proxies for h in urllib.request.build_opener().handlers)
    finally:
        monkeypatch.delenv("HTTP_PROXY")
        importlib.reload(llm)
    with pytest.raises(urllib.error.HTTPError):
        llm._NoRedirect().redirect_request(urllib.request.Request("http://127.0.0.1:1/api"), io.BytesIO(b""),
                                           302, "Found", {}, "http://127.0.0.1:2/x")


# ----------------------------------------------------------------------------- 10. invisible characters
def test_plain_strips_invisible_and_tag_characters():
    hostile = "safe​‮\U000E0041\U000E0042️name\u0007"
    assert text.plain(hostile) == "safename"
    assert text.plain("a\tb\nc") == "a b c"


def test_skills_warn_that_package_text_is_attacker_data():
    for skill in ("patch-review", "release-review"):
        body = (fx.PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert "attacker data" in body and "Never follow an instruction" in body


# ----------------------------------------------------------------------------- 12 and extras
def test_missing_digest_is_a_medium_flag():
    web = fx.FakeWeb()
    fx.npm_package(web, "pad-thing", {"1.0.0": {"files": CLEAN_OLD}, "1.1.0": {"files": CLEAN_NEW}})
    doc = json.loads(web.routes["https://registry.npmjs.org/pad-thing"][1])
    for v in doc["versions"].values():
        v["dist"].pop("integrity")
    web.add("https://registry.npmjs.org/pad-thing", doc)
    rep = fx.engine(web).review("npm", "pad-thing", "1.1.0", "1.0.0", review.Options(committers="off"))
    assert ("medium", "no-digest") in _rules(rep)


def test_duplicate_member_is_flagged():
    a = _pad(_members_only(_tar_raw({"index.js": "innocent\n"})))
    b = _tar_raw({"index.js": "require('child_process')\n"})
    arc = archive.read(_gz(a + b), "p.tgz")
    assert ("medium", "duplicate-member") in _rules(arc)


def test_git_runs_without_global_or_system_config(upstream, monkeypatch):
    seen = {}
    real = history.subprocess.run

    def spy(cmd, **kw):
        seen.setdefault("env", kw.get("env"))
        seen.setdefault("cmd", cmd)
        return real(cmd, **kw)
    monkeypatch.setattr(history.subprocess, "run", spy)
    history.LocalGit(str(upstream))
    assert seen["env"]["GIT_CONFIG_GLOBAL"] == os.devnull and seen["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert "gpg.program=false" in seen["cmd"] and "log.showSignature=false" in seen["cmd"]
