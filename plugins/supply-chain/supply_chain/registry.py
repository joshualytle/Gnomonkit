"""Registry metadata for one package: every version's artifact, digest, publisher and date.

Public metadata only, from npm, PyPI, crates.io and pub.dev. Each registry publishes different
facts; one it does not publish stays None and lands in the report's "not checked" list, instead
of reading as fine:

  publisher   npm (_npmUser) and crates.io (published_by). PyPI and pub.dev do not say who
              uploaded a release.
  maintainers npm only.
  scripts     npm only (lifecycle scripts in the version's package.json).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from urllib.parse import quote

from .netfetch import HTTP, FetchError

ECOSYSTEMS = ("npm", "pypi", "cargo", "pub")
_ALIASES = {"crates": "cargo", "crates.io": "cargo", "rust": "cargo", "pip": "pypi", "python": "pypi",
            "dart": "pub", "pub.dev": "pub", "flutter": "pub", "node": "npm", "js": "npm"}


def eco_name(s: str) -> str:
    s = (s or "").lower()
    return _ALIASES.get(s, s)


@dataclass
class Artifact:
    url: str
    filename: str
    kind: str                         # npm-tgz | sdist | wheel | crate | pub-tgz
    sha256: "str | None" = None
    sha512_b64: "str | None" = None   # npm integrity
    sha1: "str | None" = None         # npm legacy shasum


@dataclass
class Version:
    version: str
    published: "dt.datetime | None" = None
    publisher: "str | None" = None
    maintainers: "frozenset | None" = None
    artifacts: list = field(default_factory=list)
    install_scripts: dict = field(default_factory=dict)
    yanked: bool = False
    deprecated: "str | None" = None
    repo_url: "str | None" = None


@dataclass
class Package:
    eco: str
    name: str
    versions: dict = field(default_factory=dict)       # version string -> Version
    latest: "str | None" = None
    created: "dt.datetime | None" = None
    repo_url: "str | None" = None
    page: str = ""
    not_checked: list = field(default_factory=list)

    def ordered(self) -> list:
        """Versions oldest first: by publish time where known, else by version number."""
        def key(v: Version):
            t = v.published.timestamp() if v.published else float("inf")
            return (t, vkey(v.version))
        return [v.version for v in sorted(self.versions.values(), key=key)]

    def previous(self, version: str):
        """The release published just before `version` on the same track: a stable release's
        predecessor is the newest earlier stable one."""
        order = self.ordered()
        if version not in order:
            return None
        pre = is_prerelease(version)
        for v in reversed(order[:order.index(version)]):
            if pre or not is_prerelease(v):
                return v
        return None


def vkey(v: str):
    return [(0, int(x), "") if x.isdigit() else (1, 0, x) for x in re.split(r"[.+_-]", str(v))]


def is_prerelease(v: str) -> bool:
    return bool(re.search(r"(?i)\d[-.]?(?:a|b|rc|alpha|beta|pre|preview|dev|canary|next)\d*", str(v)) or "-" in str(v))


def _ts(s):
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


_GH = re.compile(r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/#?].*)?$", re.I)


def github_repo(url) -> "str | None":
    """owner/name from any GitHub-shaped repository URL, or None."""
    if not url or not isinstance(url, str):
        return None
    u = url.strip()
    m = re.match(r"^(?:github:)([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$", u)
    if m:
        return "%s/%s" % (m.group(1), m.group(2))
    m = _GH.search(u)
    if not m:
        return None
    return "%s/%s" % (m.group(1), m.group(2))


class Registries:
    def __init__(self, http: HTTP) -> None:
        self.http = http
        self._memo: dict = {}

    def package(self, eco: str, name: str) -> Package:
        """Raises FetchError (lookup failed) or LookupError (no such package)."""
        eco = eco_name(eco)
        key = (eco, name)
        if key not in self._memo:
            fn = {"npm": self._npm, "pypi": self._pypi, "cargo": self._cargo, "pub": self._pub}.get(eco)
            if fn is None:
                raise LookupError("no registry adapter for %s (supported: %s)" % (eco, ", ".join(ECOSYSTEMS)))
            try:
                self._memo[key] = fn(name)
            except (KeyError, TypeError, AttributeError, ValueError) as e:
                raise FetchError("unexpected %s registry response (%s)" % (eco, type(e).__name__)) from None
        return self._memo[key]

    def version_repo(self, pkg: Package, version: "str | None") -> "str | None":
        """The repository URL one specific version's own metadata names. None when the registry keeps
        only a package-level value (crates.io), or the lookup fails."""
        if not version:
            return None
        if pkg.eco in ("npm", "pub"):
            v = pkg.versions.get(version)
            return v.repo_url if v else None
        if pkg.eco == "pypi":
            try:
                d = self.http.json("https://pypi.org/pypi/%s/%s/json" % (quote(pkg.name), quote(version))) or {}
            except FetchError:
                return None
            info = d.get("info") or {}
            urls = info.get("project_urls") or {}
            return next((u for u in urls.values() if isinstance(u, str) and "github.com" in u),
                        info.get("home_page") if "github.com" in str(info.get("home_page") or "") else None)
        return None

    # ------------------------------------------------------------------------- npm
    def _npm(self, name: str) -> Package:
        doc = self.http.json("https://registry.npmjs.org/%s" % quote(name, safe="@"), max_bytes=80_000_000)
        if doc is None:
            raise LookupError("%s is not on the npm registry" % name)
        p = Package("npm", name, page="https://www.npmjs.com/package/%s" % name)
        t = doc.get("time") or {}
        p.created = _ts(t.get("created"))
        p.latest = (doc.get("dist-tags") or {}).get("latest")
        repo = doc.get("repository")
        p.repo_url = repo.get("url") if isinstance(repo, dict) else (repo if isinstance(repo, str) else None)
        for ver, meta in (doc.get("versions") or {}).items():
            if not isinstance(meta, dict):
                continue
            dist = meta.get("dist") or {}
            integ = str(dist.get("integrity") or "")
            art = Artifact(str(dist.get("tarball") or ""), "%s-%s.tgz" % (name.split("/")[-1], ver), "npm-tgz",
                           sha512_b64=integ[7:] if integ.startswith("sha512-") else None,
                           sha1=dist.get("shasum"))
            scripts = meta.get("scripts") or {}
            inst = {k: str(scripts[k])[:300] for k in ("preinstall", "install", "postinstall", "prepare")
                    if k in scripts}
            mrepo = meta.get("repository")
            v = Version(ver, published=_ts(t.get(ver)),
                        publisher=(meta.get("_npmUser") or {}).get("name"),
                        maintainers=frozenset(m.get("name") for m in (meta.get("maintainers") or [])
                                              if isinstance(m, dict) and m.get("name")) or None,
                        artifacts=[art] if art.url else [],
                        install_scripts=inst,
                        deprecated=str(meta["deprecated"])[:200] if meta.get("deprecated") else None,
                        repo_url=mrepo.get("url") if isinstance(mrepo, dict) else (mrepo if isinstance(mrepo, str) else None))
            p.versions[ver] = v
        return p

    # ------------------------------------------------------------------------- PyPI
    def _pypi(self, name: str) -> Package:
        doc = self.http.json("https://pypi.org/pypi/%s/json" % quote(name))
        if doc is None:
            raise LookupError("%s is not on PyPI" % name)
        info = doc.get("info") or {}
        p = Package("pypi", info.get("name") or name, page="https://pypi.org/project/%s/" % name)
        p.latest = info.get("version")
        urls = info.get("project_urls") or {}
        p.repo_url = next((u for u in urls.values() if isinstance(u, str) and "github.com" in u),
                          info.get("home_page") if "github.com" in str(info.get("home_page") or "") else None)
        first = None
        for ver, files in (doc.get("releases") or {}).items():
            arts, times = [], []
            for f in files or []:
                pt = f.get("packagetype")
                kind = "wheel" if pt == "bdist_wheel" else ("sdist" if pt == "sdist" else None)
                if kind is None:
                    continue
                arts.append(Artifact(f.get("url") or "", f.get("filename") or "", kind,
                                     sha256=(f.get("digests") or {}).get("sha256")))
                tt = _ts(f.get("upload_time_iso_8601"))
                if tt:
                    times.append(tt)
            if not arts:
                continue
            published = min(times) if times else None
            if published and (first is None or published < first):
                first = published
            p.versions[ver] = Version(ver, published=published, artifacts=arts,
                                      yanked=all(f.get("yanked") for f in files))
        p.created = first
        p.not_checked.append("PyPI does not publish who uploaded a release, so a new publisher is not checked")
        return p

    # ------------------------------------------------------------------------- crates.io
    def _cargo(self, name: str) -> Package:
        doc = self.http.json("https://crates.io/api/v1/crates/%s" % quote(name))
        if doc is None:
            raise LookupError("%s is not on crates.io" % name)
        crate = doc.get("crate") or {}
        p = Package("cargo", crate.get("name") or name, page="https://crates.io/crates/%s" % name)
        p.created = _ts(crate.get("created_at"))
        p.latest = crate.get("max_stable_version") or crate.get("newest_version")
        p.repo_url = crate.get("repository")
        versions = [v for v in (doc.get("versions") or []) if isinstance(v, dict)]
        if not versions:                       # the API can leave the list out; fetch it by page
            for page in range(1, 11):
                d = self.http.json("https://crates.io/api/v1/crates/%s/versions?per_page=100&page=%d"
                                   % (quote(name), page)) or {}
                got = [v for v in (d.get("versions") or []) if isinstance(v, dict)]
                versions += got
                if len(got) < 100:
                    break
        for v in versions:
            num = v.get("num")
            if not num:
                continue
            url = "https://static.crates.io/crates/%s/%s-%s.crate" % (quote(name), quote(name), quote(num))
            p.versions[num] = Version(num, published=_ts(v.get("created_at")),
                                      publisher=(v.get("published_by") or {}).get("login"),
                                      artifacts=[Artifact(url, "%s-%s.crate" % (name, num), "crate",
                                                          sha256=v.get("checksum"))],
                                      yanked=bool(v.get("yanked")))
        return p

    # ------------------------------------------------------------------------- pub.dev
    def _pub(self, name: str) -> Package:
        doc = self.http.json("https://pub.dev/api/packages/%s" % quote(name))
        if doc is None:
            raise LookupError("%s is not on pub.dev" % name)
        p = Package("pub", name, page="https://pub.dev/packages/%s" % name)
        latest = doc.get("latest") or {}
        p.latest = latest.get("version")
        spec = latest.get("pubspec") or {}
        p.repo_url = spec.get("repository") or spec.get("homepage")
        times = []
        for v in doc.get("versions") or []:
            ver = v.get("version")
            if not ver or not v.get("archive_url"):
                continue
            t = _ts(v.get("published"))
            if t:
                times.append(t)
            vspec = v.get("pubspec") or {}
            p.versions[ver] = Version(ver, published=t,
                                      artifacts=[Artifact(v["archive_url"], "%s-%s.tar.gz" % (name, ver), "pub-tgz",
                                                          sha256=v.get("archive_sha256"))],
                                      repo_url=vspec.get("repository") or vspec.get("homepage"))
        p.created = min(times) if times else None
        if doc.get("isDiscontinued"):
            for v in p.versions.values():
                v.deprecated = "discontinued on pub.dev"
        p.not_checked.append("pub.dev does not publish who uploaded a release, so a new publisher is not checked")
        return p


def pick_artifacts(eco: str, old: "Version | None", new: Version, prefer: str = "auto"):
    """(old artifact or None, new artifact): the same kind for both sides, so the diff compares
    like with like. PyPI: an sdist when both have one (it holds setup code that runs at
    install), else a pure-Python wheel, else the first wheel whose filename tag matches."""
    if eco != "pypi":
        a = new.artifacts[0] if new.artifacts else None
        b = old.artifacts[0] if old and old.artifacts else None
        return b, a

    def by_kind(v, kind):
        return [a for a in (v.artifacts if v else []) if a.kind == kind]

    def tag(a):
        parts = a.filename[:-4].split("-") if a.filename.endswith(".whl") else []
        return "-".join(parts[-3:]) if len(parts) >= 5 else a.filename

    order = {"sdist": ["sdist", "wheel"], "wheel": ["wheel", "sdist"]}.get(prefer, ["sdist", "wheel"])
    for kind in order:
        n = by_kind(new, kind)
        if not n:
            continue
        if kind == "wheel":
            pure = [a for a in n if tag(a).endswith("none-any")]
            n = pure or n
        o = by_kind(old, kind)
        if old is None:
            return None, n[0]
        if not o:
            continue
        if kind == "wheel":
            for a in n:
                same = [b for b in o if tag(b) == tag(a)]
                if same:
                    return same[0], a
            continue
        return o[0], n[0]
    return (old.artifacts[0] if old and old.artifacts else None), (new.artifacts[0] if new.artifacts else None)


__all__ = ["Artifact", "Version", "Package", "Registries", "FetchError", "pick_artifacts", "github_repo",
           "eco_name", "ECOSYSTEMS", "is_prerelease", "vkey"]
