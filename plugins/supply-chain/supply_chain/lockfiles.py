"""Parse lockfiles and manifests into {(name, version): Pkg} so base and head can be diffed.

Only data formats are read, with the standard library: JSON, TOML (Python 3.11+), and small line
parsers for the two YAML lockfiles (pnpm, pub), so no PyYAML is needed. Nothing is installed or
executed. A file that cannot be read raises LockfileError: the review then says "could not
parse", never "nothing new".

Manifests (package.json, pyproject.toml, Cargo.toml, pubspec.yaml) name ranges, not versions.
A range's floor stands in for the version (`^1.4.0` reads as 1.4.0), and the Pkg is marked
`from_range`, so the report can say the reviewed version is the floor, not what installs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

try:
    import tomllib
except ImportError:                     # Python < 3.11: TOML formats report "could not parse"
    tomllib = None


class LockfileError(ValueError):
    pass


@dataclass
class Pkg:
    eco: str                    # npm | pypi | cargo | pub | go
    name: str
    version: str
    direct: "bool | None" = None           # None = the format does not say
    install_script: "bool | None" = None   # lockfile's own signal (npm hasInstallScript, pnpm requiresBuild)
    source_only: "bool | None" = None      # python: no wheel, so setup code runs at install
    from_range: bool = False               # a manifest range floor, not a resolved version


_KINDS = (
    (re.compile(r"(?:^|/)package-lock\.json$|(?:^|/)npm-shrinkwrap\.json$"), "npm-lock"),
    (re.compile(r"(?:^|/)pnpm-lock\.yaml$"), "pnpm-lock"),
    (re.compile(r"(?:^|/)yarn\.lock$"), "yarn-lock"),
    (re.compile(r"(?:^|/)uv\.lock$"), "uv-lock"),
    (re.compile(r"(?:^|/)poetry\.lock$"), "poetry-lock"),
    (re.compile(r"(?:^|/)(?:requirements[^/]*\.(?:txt|lock|in)|constraints[^/]*\.txt)$"), "requirements"),
    (re.compile(r"(?:^|/)go\.sum$"), "go-sum"),
    (re.compile(r"(?:^|/)Cargo\.lock$"), "cargo-lock"),
    (re.compile(r"(?:^|/)pubspec\.lock$"), "pub-lock"),
    (re.compile(r"(?:^|/)package\.json$"), "package-json"),
    (re.compile(r"(?:^|/)pyproject\.toml$"), "pyproject"),
    (re.compile(r"(?:^|/)Cargo\.toml$"), "cargo-toml"),
    (re.compile(r"(?:^|/)pubspec\.yaml$"), "pubspec-yaml"),
)
MANIFESTS = {"package-json", "pyproject", "cargo-toml", "pubspec-yaml"}


def kind(path: str):
    p = path.replace("\\", "/")
    if "/node_modules/" in "/" + p:
        return None
    for rx, k in _KINDS:
        if rx.search(p):
            return k
    return None


def norm_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse(path: str, content: str) -> dict:
    k = kind(path)
    if k is None:
        raise LockfileError("not a lockfile or manifest: %s" % path)
    try:
        return _PARSERS[k](content)
    except LockfileError:
        raise
    except Exception as e:                                       # noqa: BLE001
        raise LockfileError("could not parse %s: %s" % (path, type(e).__name__)) from None


def _toml(content: str) -> dict:
    if tomllib is None:
        raise LockfileError("TOML needs Python 3.11 or newer")
    return tomllib.loads(content)


def _add(out: dict, p: Pkg) -> None:
    key = (p.name, p.version)
    prev = out.get(key)
    if prev is None:
        out[key] = p
        return
    prev.direct = prev.direct or p.direct
    prev.install_script = prev.install_script or p.install_script


# ----------------------------------------------------------------------------- npm family
def _npm(content: str) -> dict:
    d = json.loads(content)
    out: dict = {}
    pk = d.get("packages")
    if isinstance(pk, dict):                                    # lockfileVersion 2 / 3
        root = pk.get("", {})
        direct = set()
        for sect in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            direct |= set(root.get(sect) or {})
        for loc, meta in pk.items():
            if not loc or "node_modules/" not in loc or meta.get("link"):
                continue
            name = meta.get("name") or loc.rsplit("node_modules/", 1)[1]
            ver = meta.get("version")
            if not ver:
                continue
            top = loc.count("node_modules/") == 1 and loc.startswith("node_modules/")
            _add(out, Pkg("npm", name, ver, direct=top and name in direct,
                          install_script=bool(meta.get("hasInstallScript"))))
        return out

    def walk(deps: dict, depth: int) -> None:                   # lockfileVersion 1
        for name, meta in (deps or {}).items():
            if meta.get("version"):
                _add(out, Pkg("npm", name, meta["version"], direct=(depth == 0 and not meta.get("dev")) or None))
            walk(meta.get("dependencies") or {}, depth + 1)
    walk(d.get("dependencies") or {}, 0)
    return out


_PNPM_KEY = re.compile(r"^/?((?:@[^/@]+/)?[^/@(]+)[@/]([^(@/]+)")


def _yaml_sections(content: str):
    """Yield (top-level key, indent, stripped line) for a block-style YAML document.

    Enough for lockfiles a tool wrote: block mappings, two-space indents, no anchors."""
    top = None
    for raw in content.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0:
            top = line.split(":", 1)[0].strip("'\"")
            continue
        yield top, indent, line


def _unq(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def _pnpm(content: str) -> dict:
    out: dict = {}
    direct = set()
    importer_indent = None
    cur = None
    for top, indent, line in _yaml_sections(content):
        key = _unq(line[:-1]) if line.endswith(":") else None
        if top == "importers":
            if indent == 2:
                importer_indent = None
            elif key in ("dependencies", "devDependencies", "optionalDependencies"):
                importer_indent = indent + 2
            elif importer_indent is not None and indent == importer_indent:
                direct.add(_unq(line.split(":", 1)[0]))
            elif importer_indent is not None and indent < importer_indent:
                importer_indent = None
        elif top in ("dependencies", "devDependencies", "optionalDependencies") and indent == 2:
            direct.add(_unq(line.split(":", 1)[0]))             # lockfile v5: no importers block
        elif top == "packages":
            if indent == 2 and key is not None:
                m = _PNPM_KEY.match(key)
                cur = None
                if m:
                    cur = Pkg("npm", m.group(1), m.group(2))
                    _add(out, cur)
            elif cur is not None and indent == 4 and line.replace(" ", "") == "requiresBuild:true":
                cur.install_script = True
    for p in out.values():
        p.direct = p.name in direct
    return out


_YARN_HEAD = re.compile(r'^"?((?:@[^@/"]+/)?[^@"\s,]+)@')
_YARN_VER = re.compile(r'^\s+version:?\s+"?([^"\s]+)"?')


def _yarn(content: str) -> dict:
    out: dict = {}
    name = None
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            m = _YARN_HEAD.match(line)
            name = m.group(1) if m and not line.startswith("__metadata") else None
            continue
        m = _YARN_VER.match(line)
        if name and m:
            _add(out, Pkg("npm", name, m.group(1)))
            name = None
    return out


_RANGE_FLOOR = re.compile(r"^\s*(?:[\^~]|>=|==|=|v)?\s*(\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.-]+)?)\s*$")


def floor(spec: str):
    """The version a simple range starts at (`^1.2.3` -> 1.2.3), or None for anything else
    (git URLs, workspace:, tags, compound ranges)."""
    m = _RANGE_FLOOR.match(str(spec))
    return m.group(1) if m else None


def _package_json(content: str) -> dict:
    d = json.loads(content)
    out: dict = {}
    for sect in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, spec in (d.get(sect) or {}).items():
            v = floor(spec) if isinstance(spec, str) else None
            if v:
                exact = bool(re.match(r"^\s*v?\d", spec))
                _add(out, Pkg("npm", name, v, direct=True, from_range=not exact))
    return out


# ----------------------------------------------------------------------------- python
def _uv(content: str) -> dict:
    d = _toml(content)
    out: dict = {}
    pkgs = d.get("package") or []
    roots = [p for p in pkgs if (p.get("source") or {}).get("editable") or (p.get("source") or {}).get("virtual")]
    direct = {norm_pypi(dep.get("name", "")) for r in roots for dep in (r.get("dependencies") or [])}
    for p in pkgs:
        if p in roots or not p.get("version"):
            continue
        n = norm_pypi(p["name"])
        _add(out, Pkg("pypi", n, str(p["version"]), direct=(n in direct) if roots else None,
                      source_only=bool(p.get("sdist")) and not p.get("wheels")))
    return out


def _poetry(content: str) -> dict:
    d = _toml(content)
    out: dict = {}
    for p in d.get("package") or []:
        files = p.get("files") or []
        wheels = any(str(f.get("file", "")).endswith(".whl") for f in files)
        _add(out, Pkg("pypi", norm_pypi(p["name"]), str(p["version"]),
                      source_only=(not wheels) if files else None))
    return out


_REQ = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*===?\s*([A-Za-z0-9.+!_-]+)")


def _requirements(content: str) -> dict:
    out: dict = {}
    for line in content.splitlines():
        m = _REQ.match(line)
        if m:
            _add(out, Pkg("pypi", norm_pypi(m.group(1)), m.group(2)))
    return out


_PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*(===?|>=|~=)\s*([A-Za-z0-9.+!_-]+)")


def _pyproject(content: str) -> dict:
    d = _toml(content)
    out: dict = {}
    proj = d.get("project") or {}
    specs = list(proj.get("dependencies") or [])
    for group in (proj.get("optional-dependencies") or {}).values():
        specs += list(group or [])
    for s in specs:
        m = _PEP508.match(str(s))
        if m:
            _add(out, Pkg("pypi", norm_pypi(m.group(1)), m.group(3), direct=True,
                          from_range=m.group(2) not in ("==", "===")))
    poetry = ((d.get("tool") or {}).get("poetry") or {})
    tables = [poetry.get("dependencies") or {}, poetry.get("dev-dependencies") or {}]
    tables += [(g or {}).get("dependencies") or {} for g in (poetry.get("group") or {}).values()]
    for t in tables:
        for name, spec in t.items():
            if name.lower() == "python":
                continue
            spec = spec.get("version") if isinstance(spec, dict) else spec
            v = floor(spec) if isinstance(spec, str) else None
            if v:
                _add(out, Pkg("pypi", norm_pypi(name), v, direct=True,
                              from_range=not re.match(r"^\s*(?:==)?\s*\d", spec)))
    return out


# ----------------------------------------------------------------------------- go, rust, dart
def _gosum(content: str) -> dict:
    out: dict = {}
    for line in content.splitlines():
        parts = line.split()
        if len(parts) >= 2 and not parts[1].endswith("/go.mod"):
            _add(out, Pkg("go", parts[0], parts[1]))
    return out


def _cargo(content: str) -> dict:
    d = _toml(content)
    out: dict = {}
    for p in d.get("package") or []:
        if "source" not in p:                    # workspace members, not dependencies
            continue
        _add(out, Pkg("cargo", p["name"], str(p["version"])))
    return out


def _cargo_toml(content: str) -> dict:
    d = _toml(content)
    out: dict = {}
    tables = [d.get(k) or {} for k in ("dependencies", "dev-dependencies", "build-dependencies")]
    tables.append(((d.get("workspace") or {}).get("dependencies")) or {})
    for t in tables:
        for name, spec in t.items():
            if isinstance(spec, dict):
                if spec.get("path") or spec.get("git"):
                    continue
                name = spec.get("package") or name
                spec = spec.get("version")
            v = floor(spec) if isinstance(spec, str) else None
            if v:
                _add(out, Pkg("cargo", name, v, direct=True, from_range=not spec.strip().startswith("=")))
    return out


def _pub_lock(content: str) -> dict:
    out: dict = {}
    cur: dict = {}

    def flush():
        if cur.get("name") and cur.get("version") and cur.get("source", "hosted") == "hosted":
            dep = cur.get("dependency", "")
            _add(out, Pkg("pub", cur["name"], cur["version"],
                          direct=dep.startswith("direct") if dep else None))

    for top, indent, line in _yaml_sections(content):
        if top != "packages":
            continue
        if indent == 2 and line.endswith(":"):
            flush()
            cur.clear()
            cur["name"] = _unq(line[:-1])
        elif indent == 4 and ":" in line:
            k, v = line.split(":", 1)
            if k.strip() in ("version", "source", "dependency"):
                cur[k.strip()] = _unq(v)
    flush()
    return out


def _pubspec_yaml(content: str) -> dict:
    out: dict = {}
    pending = None
    for top, indent, line in _yaml_sections(content):
        if top not in ("dependencies", "dev_dependencies"):
            continue
        if indent == 2:
            pending = None
            name, _, spec = line.partition(":")
            spec = _unq(spec)
            if not spec:
                pending = name.strip()          # a nested block: hosted with a version, or path/git/sdk
                continue
            v = floor(spec)
            if v:
                _add(out, Pkg("pub", name.strip(), v, direct=True, from_range=not re.match(r"^\s*\d", spec)))
        elif pending and indent == 4 and line.startswith("version:"):
            spec = _unq(line.split(":", 1)[1])
            v = floor(spec)
            if v:
                _add(out, Pkg("pub", pending, v, direct=True, from_range=not re.match(r"^\s*\d", spec)))
    return out


_PARSERS = {"npm-lock": _npm, "pnpm-lock": _pnpm, "yarn-lock": _yarn, "uv-lock": _uv,
            "poetry-lock": _poetry, "requirements": _requirements, "go-sum": _gosum,
            "cargo-lock": _cargo, "pub-lock": _pub_lock, "package-json": _package_json,
            "pyproject": _pyproject, "cargo-toml": _cargo_toml, "pubspec-yaml": _pubspec_yaml}


@dataclass
class Change:
    pkg: Pkg
    kind: str                         # new (name absent from base) | bumped (new version of a known name)
    previous: "str | None"            # the highest base version for a bump
    source: str = ""                  # the lockfile or manifest it came from


def _vkey(v: str):
    return [(0, int(x)) if x.isdigit() else (1, x) for x in re.split(r"[.+-]", v)]


def diff(base: dict, head: dict, source: str = "") -> list:
    base_names: dict = {}
    for (n, v) in base:
        base_names.setdefault(n, []).append(v)
    out = []
    for key, p in sorted(head.items()):
        if key in base:
            continue
        prev = base_names.get(p.name)
        if prev:
            prev_v = sorted(prev, key=_vkey)[-1]
            out.append(Change(p, "bumped", prev_v, source))
        else:
            out.append(Change(p, "new", None, source))
    return out
