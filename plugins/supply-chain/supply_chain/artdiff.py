"""Diff two package archives file by file, and say what KIND of change each file carries.

Kinds describe; they do not flag. A release of an HTTP client adds network calls every month,
so "network" is reported as a kind of change for a reader to weigh, while rules.py decides what
is flagged. The kinds:

  install_script   runs when the package is installed (setup.py, .pth, build.rs, binding.gyp,
                   hook/build.dart, npm lifecycle scripts)
  build_file       drives the build (Makefile, configure, *.m4, CMake, meson, gradle,
                   pyproject.toml, setup.cfg, CI workflows, bundler configs)
  network          added lines that open connections or make requests
  exec             added lines that run processes, eval code or load code dynamically
  obfuscated       added lines that are minified (very long) or carry long encoded blobs
  binary           a compiled or otherwise non-text file
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from . import rules

KIND_ORDER = ("install_script", "build_file", "network", "exec", "obfuscated", "binary")
SENSITIVE = frozenset({"install_script", "build_file", "obfuscated", "binary"})

_INSTALL_PATH = re.compile(
    r"(?i)(?:^|/)(?:setup\.py|build\.rs|binding\.gyp|hook/build\.dart|(?:pre|post)?install\.(?:js|cjs|mjs|sh|ps1))$|\.pth$")
_BUILD_PATH = re.compile(
    r"(?i)(?:^|/)(?:setup\.cfg|pyproject\.toml|MANIFEST\.in|hatch_build\.py|(?:GNU)?makefile(?:\.am|\.in)?|"
    r"[^/]+\.mk|CMakeLists\.txt|[^/]+\.cmake|configure(?:\.ac|\.in)?|[^/]+\.m4|meson\.build|meson_options\.txt|"
    r"[^/]+\.gradle(?:\.kts)?|Cargo\.toml|build\.zig|BUILD(?:\.bazel)?|WORKSPACE|[^/]+\.gypi?|"
    r"(?:webpack|rollup|vite|esbuild|babel)\.config\.[cm]?[jt]s|Dockerfile|Justfile|Rakefile|[^/]+\.gemspec|"
    r"pubspec\.yaml)$|(?:^|/)\.github/workflows/[^/]+\.ya?ml$")

_NETWORK = re.compile(
    r"\bfetch\s*\(|\baxios\b|XMLHttpRequest|\brequire\(\s*['\"](?:node:)?(?:https?|net|dgram|tls)['\"]\s*\)|\bWebSocket\s*\(|\bhttps?\.(?:request|get)\s*\(|\bnet\.(?:connect|createConnection)\b|"
    r"\bdgram\.createSocket\b|\bdns\.(?:lookup|resolve\w*)\s*\(|\brequests\.(?:get|post|put|patch|delete|request|Session)\b|"
    r"\burllib\.request\b|\burlopen\s*\(|\bhttpx\.|\baiohttp\.|\bsocket\.(?:socket|create_connection)\s*\(|\bhttp\.client\b|"
    r"\bHttpClient\b|\breqwest::|\bureq::|\bTcpStream::connect\b|\bUdpSocket::|\bhttp\.(?:Get|Post|NewRequest)\b|"
    r"\b(?:curl|wget)\s+-|Invoke-WebRequest|Net\.WebClient|\bhttp\.(?:get|post)\s*\(\s*Uri")
_EXEC = re.compile(
    r"\bchild_process\b|\bexec(?:Sync|File|FileSync)?\s*\(|\bspawn(?:Sync)?\s*\(|\beval\s*\(|\bnew\s+Function\s*\(|"
    r"\bsubprocess\.\w+|\bos\.(?:system|popen|exec\w*|spawn\w*)\s*\(|\bpty\.spawn\b|\bPopen\s*\(|"
    r"\bProcess\.(?:run|start)\s*\(|\bCommand::new\b|\bstd::process\b|\bRuntime\.getRuntime\(\)\.exec\b|"
    r"\bShellExecute\w*\b|\b__import__\s*\(|\bimportlib\.import_module\s*\(|\bctypes\.|\bdlopen\s*\(|"
    r"\brequire\s*\(\s*[^'\"`\s)]|\bexec\.Command\b")
_BLOB = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}|(?:\\x[0-9a-fA-F]{2}){40,}|(?:0x[0-9a-fA-F]{2},\s*){60,}")
MINIFIED_LINE = 1000


def is_text(data: "bytes | None") -> bool:
    if data is None:
        return False
    if b"\x00" in data[:8192]:
        return False
    try:
        data[:65536].decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        return e.start > 65000      # a cut multi-byte character at the probe edge, not binary


def lines_of(data: bytes) -> list:
    return data.decode("utf-8", errors="replace").splitlines()


def added_lines(old: list, new: list) -> list:
    """(new line number, text) for lines the new file adds. Large files fall back to a set
    difference, which is linear and good enough to find what is new."""
    if len(old) * len(new) > 4_000_000 or max(len(old), len(new)) > 20000:
        have = set(old)
        return [(i + 1, ln) for i, ln in enumerate(new) if ln not in have]
    out = []
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    for op, _a1, _a2, b1, b2 in sm.get_opcodes():
        if op in ("insert", "replace"):
            out.extend((j + 1, new[j]) for j in range(b1, b2))
    return out


def removed_count(old: list, new: list) -> int:
    have = set(new)
    return sum(1 for ln in old if ln not in have)


def path_kinds(path: str) -> set:
    k = set()
    if _INSTALL_PATH.search(path):
        k.add("install_script")
    if _BUILD_PATH.search(path):
        k.add("build_file")
    return k


def content_kinds(added, path: str = "") -> set:
    """Kinds from added lines. Network and exec are code kinds: a README that mentions urllib is not
    a network call. Obfuscated content counts anywhere, test fixtures included."""
    k = set()
    code = not (path and rules.non_code(path))
    for _n, ln in added:
        s = ln[:rules.MAX_LINE]
        if len(ln) >= MINIFIED_LINE or _BLOB.search(s):
            k.add("obfuscated")
        if code and _NETWORK.search(s):
            k.add("network")
        if code and _EXEC.search(s):
            k.add("exec")
        if len(k) == 3:
            break
    return k


def ordered(kinds) -> list:
    return [k for k in KIND_ORDER if k in kinds]


@dataclass
class FileChange:
    path: str
    status: str                       # added | removed | modified
    kinds: set = field(default_factory=set)
    added: int = 0
    removed: int = 0
    size_old: "int | None" = None
    size_new: "int | None" = None
    binary: bool = False
    hits: list = field(default_factory=list)
    added_text: list = field(default_factory=list)    # (line, text), kept for the optional model pass
    note: str = ""

    def as_dict(self) -> dict:
        return {"path": self.path, "status": self.status, "kinds": ordered(self.kinds), "added": self.added,
                "removed": self.removed, "size_old": self.size_old, "size_new": self.size_new,
                "binary": self.binary, "hits": [h.as_dict() for h in self.hits], "note": self.note}


def _npm_scripts(data: "bytes | None") -> dict:
    import json
    try:
        d = json.loads((data or b"").decode("utf-8", errors="replace"))
        s = d.get("scripts") or {}
        return {k: str(s[k]) for k in ("preinstall", "install", "postinstall", "prepare") if k in s}
    except (ValueError, AttributeError):
        return {}


def diff(old_files: dict, new_files: dict) -> list:
    """FileChange for every path added, removed or changed between two archive file maps."""
    out = []
    for path in sorted(set(old_files) | set(new_files)):
        o, n = old_files.get(path), new_files.get(path)
        if o is not None and n is not None and o.sha256 == n.sha256:
            continue
        if n is None:
            out.append(FileChange(path, "removed", size_old=o.size, kinds=path_kinds(path)))
            continue
        status = "added" if o is None else "modified"
        fc = FileChange(path, status, size_old=o.size if o else None, size_new=n.size, kinds=path_kinds(path))
        if n.data is None:
            fc.note = "over %d MB: hashed, not diffed" % (n.size >> 20)
            fc.binary = True
            fc.kinds.add("binary")
            out.append(fc)
            continue
        if not is_text(n.data):
            fc.binary = True
            fc.kinds.add("binary")
            fc.hits = rules.scan_lines(path, [], status)
            out.append(fc)
            continue
        new_lines = lines_of(n.data)
        old_lines = lines_of(o.data) if (o is not None and o.data is not None and is_text(o.data)) else []
        add = added_lines(old_lines, new_lines)
        fc.added, fc.removed = len(add), removed_count(old_lines, new_lines)
        fc.kinds |= content_kinds(add, path)
        if path.endswith("package.json") and "/" not in path.strip("/"):
            if _npm_scripts(n.data) != _npm_scripts(o.data if o else None):
                fc.kinds.add("install_script")
        fc.hits = rules.scan_lines(path, add, status)
        fc.added_text = add
        out.append(fc)
    return out


def unified(old_files: dict, new_files: dict, changes: list, limit_bytes: int = 4_000_000) -> str:
    """A unified diff of the text changes, for a person to read (never parsed back)."""
    chunks, size = [], 0
    for fc in changes:
        o, n = old_files.get(fc.path), new_files.get(fc.path)
        if fc.binary:
            chunks.append("Binary file %s %s\n" % (fc.path, fc.status))
            continue
        a = lines_of(o.data) if o is not None and o.data is not None and is_text(o.data) else []
        b = lines_of(n.data) if n is not None and n.data is not None else []
        body = "\n".join(difflib.unified_diff(a, b, "a/" + fc.path, "b/" + fc.path, lineterm="", n=2)) + "\n"
        size += len(body)
        if size > limit_bytes:
            chunks.append("... diff truncated at %d bytes\n" % limit_bytes)
            break
        chunks.append(body)
    return "".join(chunks)
