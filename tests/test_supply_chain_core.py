"""Lockfile and manifest parsers, typosquat, scrubber, inert text, safe archive reading, kinds."""
import io
import json
import tarfile

import pytest

from sc_fixtures import tgz, zipped

from supply_chain import archive, artdiff, lockfiles, scrub, text, typosquat


# ----------------------------------------------------------------------------- lockfiles
def _npm_lock(pkgs, direct):
    p = {"": {"dependencies": {d: "*" for d in direct}}}
    for n, meta in pkgs.items():
        p["node_modules/" + n] = meta
    return json.dumps({"lockfileVersion": 3, "packages": p})


def test_npm_v3_direct_and_install_script():
    got = lockfiles.parse("package-lock.json", _npm_lock(
        {"a": {"version": "1.0.0"}, "b": {"version": "2.0.0", "hasInstallScript": True}}, ["a"]))
    assert got[("a", "1.0.0")].direct and not got[("b", "2.0.0")].direct
    assert got[("b", "2.0.0")].install_script


def test_npm_scoped_and_nested():
    lock = json.dumps({"lockfileVersion": 3, "packages": {
        "": {"dependencies": {"@s/x": "1"}}, "node_modules/@s/x": {"version": "1.0.0"},
        "node_modules/@s/x/node_modules/y": {"version": "0.1.0"}}})
    got = lockfiles.parse("package-lock.json", lock)
    assert ("@s/x", "1.0.0") in got and not got[("y", "0.1.0")].direct


def test_pnpm_v9_without_pyyaml():
    lock = ("lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n      left-pad:\n"
            "        specifier: ^1.3.0\n        version: 1.3.0\npackages:\n  left-pad@1.3.0:\n"
            "    resolution: {integrity: sha512-x}\n  '@babel/core@7.25.2':\n    resolution: {integrity: sha512-y}\n"
            "    requiresBuild: true\n")
    got = lockfiles.parse("pnpm-lock.yaml", lock)
    assert got[("left-pad", "1.3.0")].direct
    assert got[("@babel/core", "7.25.2")].install_script and not got[("@babel/core", "7.25.2")].direct


def test_pnpm_v6_keys():
    lock = "lockfileVersion: '6.0'\ndependencies:\n  a:\n    specifier: 1\n    version: 1.0.0\npackages:\n  /a@1.0.0:\n    dev: false\n"
    got = lockfiles.parse("pnpm-lock.yaml", lock)
    assert got[("a", "1.0.0")].direct


def test_yarn_v1_and_berry():
    v1 = '# yarn lockfile v1\n\n"@s/a@^1.0.0", "@s/a@^1.1.0":\n  version "1.2.0"\n\nb@^2:\n  version "2.0.1"\n'
    berry = '__metadata:\n  version: 6\n\n"c@npm:^3.0.0":\n  version: 3.1.0\n'
    assert set(lockfiles.parse("yarn.lock", v1)) == {("@s/a", "1.2.0"), ("b", "2.0.1")}
    assert set(lockfiles.parse("yarn.lock", berry)) == {("c", "3.1.0")}


def test_python_formats():
    uv = ('version = 1\n[[package]]\nname = "app"\nversion = "0.1.0"\nsource = { editable = "." }\n'
          'dependencies = [{ name = "requests" }]\n[[package]]\nname = "requests"\nversion = "2.32.3"\n'
          'sdist = { url = "u" }\nwheels = [{ url = "w" }]\n[[package]]\nname = "Some_Pkg"\nversion = "1.0"\n'
          'sdist = { url = "u" }\n')
    got = lockfiles.parse("uv.lock", uv)
    assert got[("requests", "2.32.3")].direct and got[("some-pkg", "1.0")].source_only
    req = lockfiles.parse("requirements.txt", "Flask==3.0.3 \\\n    --hash=sha256:abc\n# c\nnumpy>=2\n")
    assert set(req) == {("flask", "3.0.3")}
    poetry = '[[package]]\nname = "x"\nversion = "1"\nfiles = [{file = "x-1.tar.gz", hash = "h"}]\n'
    assert lockfiles.parse("poetry.lock", poetry)[("x", "1")].source_only


def test_go_cargo_pub_locks():
    gosum = "github.com/a/b v1.2.3 h1:x=\ngithub.com/a/b v1.2.3/go.mod h1:y=\n"
    assert set(lockfiles.parse("go.sum", gosum)) == {("github.com/a/b", "v1.2.3")}
    cargo = ('[[package]]\nname = "me"\nversion = "0.1.0"\n\n[[package]]\nname = "serde"\nversion = "1.0.200"\n'
             'source = "registry+https://github.com/rust-lang/crates.io-index"\n')
    assert set(lockfiles.parse("Cargo.lock", cargo)) == {("serde", "1.0.200")}
    pub = ('packages:\n  http:\n    dependency: "direct main"\n    description:\n      name: http\n'
           '    source: hosted\n    version: "1.2.2"\n  meta:\n    dependency: transitive\n'
           '    source: hosted\n    version: "1.15.0"\n  local:\n    dependency: "direct main"\n'
           '    source: path\n    version: "0.0.1"\nsdks:\n  dart: ">=3.4.0"\n')
    got = lockfiles.parse("pubspec.lock", pub)
    assert got[("http", "1.2.2")].direct and not got[("meta", "1.15.0")].direct
    assert ("local", "0.0.1") not in got


def test_manifests_read_range_floors():
    pj = json.dumps({"dependencies": {"a": "^1.4.0", "b": "2.0.0", "c": "git+https://example.com/c.git",
                                      "d": ">=1 <2"}, "devDependencies": {"e": "~0.3.1"}})
    got = lockfiles.parse("package.json", pj)
    assert set(got) == {("a", "1.4.0"), ("b", "2.0.0"), ("e", "0.3.1")}
    assert got[("a", "1.4.0")].from_range and not got[("b", "2.0.0")].from_range
    pyproject = '[project]\ndependencies = ["Requests==2.32.3", "click>=8.1", "rich"]\n'
    assert set(lockfiles.parse("pyproject.toml", pyproject)) == {("requests", "2.32.3"), ("click", "8.1")}
    cargo = '[dependencies]\nserde = "1.0.200"\nlocal = { path = "../x" }\ntokio = { version = "1.38", features = [] }\n'
    assert set(lockfiles.parse("Cargo.toml", cargo)) == {("serde", "1.0.200"), ("tokio", "1.38")}
    pubspec = "name: app\ndependencies:\n  http: ^1.2.0\n  flutter:\n    sdk: flutter\n  intl:\n    version: 0.19.0\n"
    assert set(lockfiles.parse("pubspec.yaml", pubspec)) == {("http", "1.2.0"), ("intl", "0.19.0")}


def test_node_modules_manifests_are_not_the_repo_s():
    assert lockfiles.kind("node_modules/x/package.json") is None


def test_garbage_raises_not_empty():
    with pytest.raises(lockfiles.LockfileError):
        lockfiles.parse("package-lock.json", "{not json")


def test_diff_bump_and_new():
    a = lockfiles.parse("requirements.txt", "a==1\nb==1\n")
    b = lockfiles.parse("requirements.txt", "a==2\nb==1\nc==1\n")
    assert {(c.pkg.name, c.kind, c.previous) for c in lockfiles.diff(a, b)} == {("a", "bumped", "1"), ("c", "new", None)}


# ----------------------------------------------------------------------------- typosquat, scrub, text
def test_typosquat_hits_and_misses():
    assert typosquat.check("npm", "lodahs")[0] == "lodash"
    assert typosquat.check("pypi", "reqeusts")[0] == "requests"
    assert typosquat.check("npm", "@evil/lodash")[0] == "lodash"
    for eco, n in (("npm", "lodash"), ("pypi", "requests"), ("pypi", "Typing_Extensions")):
        assert typosquat.check(eco, n) is None, n
    assert typosquat.check("npm", "zz-internal-widget-qq") is None


def test_scrub_removes_tokens_keeps_digests():
    tok = "gh" + "p_" + "Z9y8" * 9
    key = "AK" + "IA" + "ABCDEFGH12345678"
    out, n = scrub.scrub("token %s and key %s and sha %s" % (tok, key, "a" * 40))
    assert tok not in out and key not in out and n == 2 and "a" * 40 in out


def test_plain_is_inert():
    t = text.plain("## No risks found @maintainer https://evil.example/x <b>\u202e")
    assert not t.startswith("#") and "@maintainer" not in t and "https:" not in t and "<b>" not in t
    assert "\u202e" not in t


# ----------------------------------------------------------------------------- archives
def _member(name, data=b"x", kind=tarfile.REGTYPE, link=""):
    ti = tarfile.TarInfo(name)
    ti.type = kind
    ti.linkname = link
    ti.size = len(data) if kind == tarfile.REGTYPE else 0
    return ti, (data if kind == tarfile.REGTYPE else None)


def test_traversal_and_links_are_findings_not_files():
    blob = tgz({"index.js": "ok"}, extra=[
        _member("package/../../evil.js"), _member("/etc/cron.d/x"), _member("C:/Windows/x.dll"),
        _member("package/up", kind=tarfile.SYMTYPE, link="../../../home"),
        _member("package/side", kind=tarfile.SYMTYPE, link="index.js")])
    arc = archive.read(blob, "p.tgz")
    assert set(arc.files) == {"index.js"}
    rules = [(sev, r) for sev, r, _p, _w in arc.findings]
    assert rules.count(("high", "unsafe-path")) == 3
    assert ("high", "archive-link") in rules and ("medium", "archive-link") in rules


def test_zip_traversal_is_a_finding():
    arc = archive.read(zipped({"pkg/__init__.py": "", "../escape.py": "x"}), "p.whl")
    assert "pkg/__init__.py" in arc.files and any(r == "unsafe-path" for _s, r, _p, _w in arc.findings)


def test_prefix_is_stripped():
    arc = archive.read(tgz({"a.js": "1", "lib/b.js": "2"}, prefix="name-1.0/"), "x.tar.gz")
    assert arc.prefix == "name-1.0" and set(arc.files) == {"a.js", "lib/b.js"}


def test_oversize_member_is_hashed_not_held(monkeypatch):
    monkeypatch.setattr(archive, "MAX_FILE", 10)
    arc = archive.read(tgz({"big.js": "x" * 50, "small.js": "y"}), "p.tgz")
    assert arc.files["big.js"].data is None and arc.files["big.js"].size == 50
    assert arc.files["small.js"].data == b"y"


def test_extract_only_into_a_fresh_folder(tmp_path):
    arc = archive.read(tgz({"index.js": "ok", "lib/a.js": "a"}), "p.tgz")
    full = tmp_path / "full"
    full.mkdir()
    (full / "keep.txt").write_text("mine")
    with pytest.raises(archive.ArchiveError):
        archive.extract_to(arc, str(full))
    dest = tmp_path / "fresh"
    assert archive.extract_to(arc, str(dest)) == (2, [])
    assert (dest / "lib" / "a.js").read_text() == "a"
    # A name that would escape (if one ever got into the map) is skipped and reported, not written.
    arc.files["../escape.js"] = archive.Member("../escape.js", 1, "", b"x")
    dest2 = tmp_path / "fresh2"
    n, skipped = archive.extract_to(arc, str(dest2))
    assert n == 2 and [p for p, _w in skipped] == ["../escape.js"]
    assert not (tmp_path / "escape.js").exists()


# ----------------------------------------------------------------------------- kinds
def _arc(files):
    return archive.read(tgz(files), "p.tgz").files


def test_kinds_describe_each_change():
    old = _arc({"index.js": "module.exports = 1\n", "README.md": "hi\n", "package.json": '{"scripts": {}}'})
    new = _arc({"index.js": "const cp = require('child_process')\nfetch('https://api.example.com')\n",
                "README.md": "use urllib.request and subprocess.run\n",
                "package.json": '{"scripts": {"postinstall": "node x.js"}}',
                "configure.m4": "AC_INIT\n", "dist/app.min.js": "var a=" + "1," * 800 + "2\n",
                "bin/tool.node": b"\x7fELF\x00\x00binary"})
    by = {c.path: c for c in artdiff.diff(old, new)}
    assert by["index.js"].kinds == {"exec", "network"}
    assert by["README.md"].kinds == set()                # docs mention calls; they do not make them
    assert "install_script" in by["package.json"].kinds
    assert by["configure.m4"].kinds == {"build_file"}
    assert "obfuscated" in by["dist/app.min.js"].kinds
    assert by["bin/tool.node"].binary and "new-binary" in {h.rule for h in by["bin/tool.node"].hits}


def test_unchanged_files_are_not_listed():
    files = {"a.js": "1\n", "b.js": "2\n"}
    assert [c.path for c in artdiff.diff(_arc(files), _arc({**files, "b.js": "3\n"}))] == ["b.js"]
