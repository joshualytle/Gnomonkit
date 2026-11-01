#!/usr/bin/env python3
"""leakcheck -- keep private data out of this repository.

Gnomonkit is built in the open-to-be. Nothing personal may enter it: no names, products, domains,
hosts, private network addresses, home-directory paths, real e-mail addresses, data files, secrets.
This check runs as a git pre-commit hook and in CI, and it FAILS CLOSED.

What it checks
  1. Blocked files: data-shaped names/extensions (session logs, databases, archives, keys, model
     weights, benchmark cases), binaries, and anything over the size cap.
  2. Built-in private patterns (no configuration needed): private IPv4 ranges, home-directory paths
     with a real user name, e-mail addresses other than example/noreply ones.
  3. A PRIVATE DENYLIST of the maintainer's own identifiers. The list lives OUTSIDE this repo and is
     passed in with --denylist or the GNOMONKIT_DENYLIST environment variable. Missing list = error
     (exit 2), never a silent pass. `--builtin-only` skips it on purpose, for runs that cannot have
     it (a pull request from a fork, which gets no secrets); it is never the default.

Output never contains the matched text -- only `path:line: category` -- so CI logs cannot leak the
very identifiers the denylist protects.

Denylist format (UTF-8 text): one rule per line, `category<TAB>regex`; `#` starts a comment.
Matching is case-insensitive. Repo-level exceptions go in `.leakcheck-allow` (one regex per line):
matching text is removed before the denylist and built-in patterns run.

Modes
  --all          every tracked file (CI)
  --staged       the staged version of every added/changed file (pre-commit hook)
  --stdin NAME   text on stdin, reported as NAME (e.g. commit messages)
  PATHS...       specific files

Exit codes: 0 clean, 1 findings, 2 configuration error.
"""
import argparse
import os
import re
import subprocess
import sys

MAX_BYTES = 1024 * 1024

BLOCKED_EXT = {
    ".jsonl", ".ndjson", ".log", ".db", ".sqlite", ".sqlite3", ".parquet", ".pkl", ".pickle", ".npy",
    ".npz", ".bin", ".onnx", ".gguf", ".safetensors", ".pt", ".ckpt", ".pem", ".key", ".p12", ".pfx",
    ".age", ".gpg", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".har", ".env", ".dump", ".bak",
}
BLOCKED_NAME = [
    re.compile(r"(^|/)\.env(\..*)?$"),
    re.compile(r"(^|/)cases[^/]*\.json$", re.I),          # benchmark cases built from real sessions
    re.compile(r"(^|/)[^/]*transcript[^/]*$", re.I),
    re.compile(r"(^|/)(data|sessions|exports?)/", re.I),   # data directories never belong here
    re.compile(r"(^|/)\.claude/projects/"),
    re.compile(r"(^|/)chroma[^/]*$", re.I),
]
# Documentation-safe addresses (RFC 5737 TEST-NETs) are not in these ranges, so docs can use them.
PRIVATE_IP = re.compile(
    r"(?<![\d.])(10(?:\.\d{1,3}){3}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}|192\.168(?:\.\d{1,3}){2})(?![\d.])")
HOME_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/]+Users[\\/]+|/Users/|/home/)"
    r"(?!(?:<|%|\$|\{|Public|Default|USERNAME|USER|you|your-?name|me|example|runner|ghrunner)\b)[A-Za-z0-9._-]{2,}",
    re.I)
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
EMAIL_OK = re.compile(r"@(example\.(com|org|net)|users\.noreply\.github\.com|anthropic\.com)$|^noreply@", re.I)


def fail_config(msg):
    print("leakcheck: CONFIGURATION ERROR: " + msg, file=sys.stderr)
    sys.exit(2)


def load_denylist(path):
    if not path:
        fail_config("no denylist. Pass --denylist PATH or set GNOMONKIT_DENYLIST "
                    "(the list lives outside this repo; see docs/LEAK-GATES.md).")
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError as e:
        fail_config("cannot read denylist (%s)" % e.strerror)
    rules = []
    for n, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" not in line:
            fail_config("denylist line %d: expected 'category<TAB>regex'" % n)
        cat, rx = line.split("\t", 1)
        try:
            rules.append((cat.strip() or "private", re.compile(rx.strip(), re.I)))
        except re.error:
            fail_config("denylist line %d: invalid regex" % n)
    if not rules:
        fail_config("denylist is empty")
    return rules


def load_allow(root):
    p = os.path.join(root, ".leakcheck-allow")
    if not os.path.exists(p):
        return []
    out = []
    for line in open(p, encoding="utf-8").read().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(re.compile(line, re.I))
    return out


def git(*args, root="."):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, check=True).stdout


# PNG chunk types that carry only pixels and colour handling. Anything else (tEXt, zTXt, iTXt, eXIf,
# iCCP, private chunks) can hold free text or data a reviewer cannot see, so it blocks the image.
PNG_SIG = b"\x89PNG\r\n\x1a\n"
PNG_PIXEL_CHUNKS = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"sRGB", b"gAMA", b"cHRM", b"pHYs", b"bKGD"}


def png_is_pixels_only(data):
    """True when data is a well-formed PNG whose chunks are all pixel/colour chunks."""
    if not data.startswith(PNG_SIG):
        return False
    i, end = len(PNG_SIG), len(data)
    while i + 8 <= end:
        length = int.from_bytes(data[i:i + 4], "big")
        ctype = data[i + 4:i + 8]
        if ctype not in PNG_PIXEL_CHUNKS:
            return False
        i += 12 + length
        if ctype == b"IEND":
            return i == end            # nothing may trail the image
    return False


def path_findings(path, size, data):
    out = []
    p = path.replace("\\", "/")
    ext = os.path.splitext(p)[1].lower()
    if ext in BLOCKED_EXT or any(rx.search(p) for rx in BLOCKED_NAME):
        out.append((path, 0, "blocked-file-type"))
    if size > MAX_BYTES:
        out.append((path, 0, "file-too-large"))
    if data is not None and b"\x00" in data[:8192]:
        # The one binary exception: a pixels-only PNG under assets/ (logos, screenshots for docs).
        if not (p.startswith("assets/") and ext == ".png" and png_is_pixels_only(data)):
            out.append((path, 0, "binary-file"))
    return out


def text_findings(name, text, rules, allow):
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for rx in allow:
            line = rx.sub(" ", line)
        if PRIVATE_IP.search(line):
            out.append((name, n, "private-ip"))
        if HOME_PATH.search(line):
            out.append((name, n, "home-path"))
        for m in EMAIL.finditer(line):
            if not EMAIL_OK.search(m.group(0)):
                out.append((name, n, "email"))
                break
        for cat, rx in rules:
            if rx.search(line):
                out.append((name, n, cat))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true")
    g.add_argument("--staged", action="store_true")
    g.add_argument("--stdin", metavar="NAME")
    ap.add_argument("--denylist", default=os.environ.get("GNOMONKIT_DENYLIST"))
    ap.add_argument("--builtin-only", action="store_true",
                    help="file rules and built-in patterns only, for runs that cannot have the denylist")
    ap.add_argument("--root", default=".")
    ap.add_argument("paths", nargs="*")
    a = ap.parse_args()

    rules = [] if a.builtin_only else load_denylist(a.denylist)
    allow = load_allow(a.root)
    findings = []

    if a.stdin:
        findings += text_findings(a.stdin, sys.stdin.read(), rules, allow)
    else:
        if a.staged:
            names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z", root=a.root).decode().split("\0")
            read = lambda f: git("show", ":" + f, root=a.root)
        elif a.all:
            names = git("ls-files", "-z", root=a.root).decode().split("\0")
            read = lambda f: open(os.path.join(a.root, f), "rb").read()
        else:
            names = a.paths
            read = lambda f: open(f, "rb").read()
        for f in filter(None, names):
            try:
                data = read(f)
            except (OSError, subprocess.CalledProcessError):
                continue
            pf = path_findings(f, len(data), data)
            findings += pf
            # A path is a finding too (e.g. a product name used as a directory name).
            findings += [(f, 0, c) for (_, _, c) in text_findings(f, f, rules, allow)]
            if not any(c in ("binary-file", "file-too-large") for _, _, c in pf):
                findings += text_findings(f, data.decode("utf-8", "replace"), rules, allow)

    seen = set()
    for name, line, cat in findings:
        key = (name, line, cat)
        if key in seen:
            continue
        seen.add(key)
        print("%s:%d: %s" % (name, line, cat) if line else "%s: %s" % (name, cat))
    if seen:
        print("leakcheck: %d finding(s). Matched text is never printed; open the file at the line "
              "shown. Real exceptions go in .leakcheck-allow with a comment." % len(seen), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
