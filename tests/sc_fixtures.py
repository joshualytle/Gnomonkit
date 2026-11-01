"""Synthetic registries, archives and upstream repos for the supply-chain tests.

Nothing here touches the network: FakeWeb answers only the URLs a test registers and fails any
other request loudly, so a test that would have gone online fails instead. Archives are built
in memory; upstream repositories are real git repos in a temp folder with made-up authors.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import urllib.error
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "supply-chain"
if str(PLUGIN) not in sys.path:
    sys.path.insert(0, str(PLUGIN))

from supply_chain import netfetch, review  # noqa: E402


class _Resp:
    def __init__(self, status, body):
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, n=-1):
        return self._body.read(-1 if n is None else n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeWeb:
    def __init__(self):
        self.routes = {}
        self.requests = []          # (url, headers)

    def add(self, url, body, status=200, location=None):
        """A 3xx status with `location` behaves like the safe opener refusing that redirect."""
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.routes[url] = (status, body, location)

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.requests.append((url, dict(req.header_items())))
        if url not in self.routes:
            raise urllib.error.URLError("unrouted in test: " + url)
        status, body, location = self.routes[url]
        if status >= 300:
            hdrs = {"Location": location} if location else {}
            raise urllib.error.HTTPError(url, status, "test", hdrs, io.BytesIO(b""))
        return _Resp(status, body)


def http(web, token=None):
    return netfetch.HTTP(opener=web, cache=None, github_token=token)


class NoModel:
    def summarise(self, subject, diff_text):
        return {"status": "did not run: off in tests"}


def engine(web, token=None, model=None):
    return review.Engine(http(web, token), model=model or NoModel())


# ----------------------------------------------------------------------------- archives
def tgz(files: dict, prefix: str = "package/", extra=None) -> bytes:
    """A gzipped tar. `extra` takes (TarInfo, bytes-or-None) pairs for hostile members."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, body in files.items():
            data = body.encode() if isinstance(body, str) else body
            ti = tarfile.TarInfo(prefix + path)
            ti.size = len(data)
            ti.mode = 0o644
            tf.addfile(ti, io.BytesIO(data))
        for ti, data in extra or []:
            tf.addfile(ti, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def zipped(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, body in files.items():
            zf.writestr(path, body)
    return buf.getvalue()


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sri(b: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(b).digest()).decode()


# ----------------------------------------------------------------------------- registries
def npm_package(web, name, versions: dict, repo="https://github.com/example-org/%s.git", created="2019-01-01T00:00:00Z"):
    """versions: {ver: {"files": {...}, "publisher": str, "time": iso, "scripts": {...}, "bad_digest": bool}}"""
    doc = {"name": name, "dist-tags": {"latest": list(versions)[-1]}, "time": {"created": created},
           "repository": {"type": "git", "url": repo % name.split("/")[-1]}, "versions": {}}
    for ver, spec in versions.items():
        files = dict(spec.get("files") or {})
        scripts = spec.get("scripts") or {}
        pj = {"name": name, "version": ver, "scripts": scripts}
        files.setdefault("package.json", json.dumps(pj, indent=2) + "\n")
        blob = tgz(files)
        url = "https://registry.npmjs.org/%s/-/%s-%s.tgz" % (name, name.split("/")[-1], ver)
        web.add(url, blob)
        doc["versions"][ver] = {
            "name": name, "version": ver, "scripts": scripts,
            "_npmUser": {"name": spec.get("publisher", "maintainer-one")},
            "maintainers": [{"name": m} for m in spec.get("maintainers", ["maintainer-one"])],
            "dist": {"tarball": url, "integrity": sri(b"tampered" if spec.get("bad_digest") else blob)}}
        doc["time"][ver] = spec.get("time", "2024-01-0%dT00:00:00Z" % (len(doc["time"])))
    web.add("https://registry.npmjs.org/%s" % name.replace("/", "%2F"), doc)
    return doc


def pypi_package(web, name, versions: dict):
    """versions: {ver: {"sdist": {...files} or None, "wheel": {...files} or None, "time": iso}}"""
    releases = {}
    for i, (ver, spec) in enumerate(versions.items()):
        files = []
        t = spec.get("time", "2024-02-%02dT00:00:00Z" % (i + 1))
        if spec.get("sdist") is not None:
            blob = tgz(spec["sdist"], prefix="%s-%s/" % (name, ver))
            url = "https://files.pythonhosted.org/packages/x/%s-%s.tar.gz" % (name, ver)
            web.add(url, blob)
            files.append({"filename": "%s-%s.tar.gz" % (name, ver), "url": url, "packagetype": "sdist",
                          "digests": {"sha256": sha256(blob)}, "upload_time_iso_8601": t, "yanked": False})
        if spec.get("wheel") is not None:
            blob = zipped(spec["wheel"])
            fn = "%s-%s-py3-none-any.whl" % (name, ver)
            url = "https://files.pythonhosted.org/packages/x/" + fn
            web.add(url, blob)
            files.append({"filename": fn, "url": url, "packagetype": "bdist_wheel",
                          "digests": {"sha256": sha256(blob)}, "upload_time_iso_8601": t, "yanked": False})
        releases[ver] = files
    web.add("https://pypi.org/pypi/%s/json" % name,
            {"info": {"name": name, "version": list(versions)[-1],
                      "project_urls": {"Source": "https://github.com/example-org/%s" % name}},
             "releases": releases})


def cargo_package(web, name, versions: dict):
    vs = []
    for i, (ver, spec) in enumerate(versions.items()):
        blob = tgz(spec["files"], prefix="%s-%s/" % (name, ver))
        web.add("https://static.crates.io/crates/%s/%s-%s.crate" % (name, name, ver), blob)
        vs.append({"num": ver, "checksum": sha256(blob), "created_at": "2024-03-%02dT00:00:00Z" % (i + 1),
                   "yanked": False, "published_by": {"login": spec.get("publisher", "crate-owner")}})
    web.add("https://crates.io/api/v1/crates/%s" % name,
            {"crate": {"name": name, "created_at": "2020-01-01T00:00:00Z", "max_stable_version": list(versions)[-1],
                       "repository": "https://github.com/example-org/%s" % name}, "versions": vs[::-1]})


def pub_package(web, name, versions: dict):
    vs = []
    for i, (ver, spec) in enumerate(versions.items()):
        blob = tgz(spec["files"], prefix="")
        url = "https://pub.dev/packages/%s/versions/%s.tar.gz" % (name, ver)
        web.add(url, blob)
        vs.append({"version": ver, "archive_url": url, "archive_sha256": sha256(blob),
                   "published": "2024-04-%02dT00:00:00Z" % (i + 1)})
    web.add("https://pub.dev/api/packages/%s" % name,
            {"name": name, "latest": {"version": list(versions)[-1], "pubspec": {}}, "versions": vs})


# ----------------------------------------------------------------------------- upstream git repos
def git(cwd, *args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=e)
    assert r.returncode == 0, r.stderr
    return r.stdout


BOT_EMAIL = "49699333+dependabot[bot]@users.noreply.github.com"   # the shape GitHub gives an app


def commit_as(repo, who: str, files: dict, msg: str, email: "str | None" = None):
    """Commit `files` (path -> text) as a made-up author named `who`."""
    for p, body in files.items():
        f = Path(repo, p)
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    mail = who + "@example.com" if email is None else email
    git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg,
        env={"GIT_AUTHOR_NAME": who, "GIT_AUTHOR_EMAIL": mail, "GIT_COMMITTER_NAME": who,
             "GIT_COMMITTER_EMAIL": mail})


def init_repo(path) -> Path:
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    return path
