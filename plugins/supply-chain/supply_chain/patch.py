"""Patch review: find the dependency bumps in this repository's change, and review each one.

The change is `git diff BASE [HEAD]`: by default the working tree against HEAD (a bump being made
right now), or two refs for a branch or pull request (`--base origin/main --head HEAD`). Every
changed lockfile and manifest is parsed at both sides; each package whose version moved, or
that is new, goes through the engine. Lockfile versions win over a manifest's range floor for
the same package.

Only git plumbing reads the repository (`git diff --name-only`, `git show REF:path`); nothing in
it is checked out, installed or run.
"""
from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import lockfiles, review, text

MAX_PACKAGES = 25
SUPPORTED = ("npm", "pypi", "cargo", "pub")


class GitError(RuntimeError):
    pass


def _git(repo: str, *args, check: bool = True):
    try:
        r = subprocess.run(["git", "-c", "core.fsmonitor=", "-C", repo, *args], capture_output=True, timeout=120,
                           encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GitError("git failed (%s)" % type(e).__name__) from None
    if r.returncode != 0:
        if check:
            raise GitError("git %s failed: %s" % (args[0], (r.stderr or "").strip()[:200]))
        return None
    return r.stdout


def changed_files(repo: str, base: str, head: "str | None") -> list:
    args = ["diff", "--name-only", "--no-renames", base] + ([head] if head else [])
    names = [n for n in _git(repo, *args).splitlines() if n.strip()]
    if not head:
        # Untracked lockfiles are part of a working-tree change too (a first `npm install`).
        extra = _git(repo, "ls-files", "--others", "--exclude-standard", check=False) or ""
        names += [n for n in extra.splitlines() if n.strip() and n not in names]
    return [n for n in names if lockfiles.kind(n)]


def read_at(repo: str, ref: "str | None", path: str) -> "str | None":
    if ref is None:
        p = os.path.join(repo, *path.split("/"))
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None
    return _git(repo, "show", "%s:%s" % (ref, path), check=False)


@dataclass
class Bump:
    eco: str
    name: str
    old: "str | None"
    new: str
    change: str                       # bumped | new
    source: str
    direct: "bool | None" = None
    from_range: bool = False
    install_hint: bool = False


@dataclass
class PatchResult:
    base: str
    head: str
    files: list = field(default_factory=list)
    bumps: list = field(default_factory=list)
    reports: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def find_bumps(repo: str, base: str, head: "str | None") -> PatchResult:
    res = PatchResult(base, head or "working tree")
    res.files = changed_files(repo, base, head)
    found: dict = {}
    for path in res.files:
        before, after = read_at(repo, base, path), read_at(repo, head, path)
        if after is None:
            continue                                   # file removed: nothing new to review
        try:
            a = lockfiles.parse(path, before) if before else {}
            b = lockfiles.parse(path, after)
        except lockfiles.LockfileError as e:
            res.errors.append(text.plain(str(e), 200))
            continue
        is_manifest = lockfiles.kind(path) in lockfiles.MANIFESTS
        for ch in lockfiles.diff(a, b, path):
            p = ch.pkg
            key = (p.eco, p.name)
            bump = Bump(p.eco, p.name, ch.previous, p.version, ch.kind, path, p.direct, p.from_range,
                        bool(p.install_script or p.source_only))
            prev = found.get(key)
            if prev is None or (prev.from_range and not bump.from_range) or \
                    (lockfiles.kind(prev.source) in lockfiles.MANIFESTS and not is_manifest):
                if prev is not None and prev.direct and bump.direct is not True:
                    bump.direct = True
                found[key] = bump
    for b in found.values():
        if b.eco not in SUPPORTED:
            res.skipped.append("%s %s: %s artifacts are not reviewed yet" % (b.eco, text.code(b.name), b.eco))
        else:
            res.bumps.append(b)
    # Direct dependencies and install-time code first; the cap drops the least interesting.
    res.bumps.sort(key=lambda b: (not b.direct, not b.install_hint, b.change != "new", b.eco, b.name))
    return res


def run(engine: review.Engine, repo: str, base: str = "HEAD", head: "str | None" = None,
        opt: "review.Options | None" = None, max_packages: int = MAX_PACKAGES, committer_checks: int = 5,
        workers: int = 4) -> PatchResult:
    opt = opt or review.Options()
    try:
        res = find_bumps(repo, base, head)
    except GitError as e:
        res = PatchResult(base, head or "working tree")
        res.errors.append(text.plain(str(e), 200))
        return res
    todo = res.bumps[:max_packages]
    for b in res.bumps[max_packages:]:
        res.skipped.append("%s %s %s: over the --max %d cap" % (b.eco, text.code(b.name), text.plain(b.new, 40), max_packages))

    def one(i_b):
        # One hostile package must not take the whole review down with it (pool.map re-raises the
        # first exception and discards every other result).
        try:
            return _one(i_b)
        except Exception as e:                                   # noqa: BLE001
            _i, b = i_b
            r = review.Report(b.eco, b.name, b.old, b.new, change=b.change)
            r.direct, r.from_range, r.lockfile = b.direct, b.from_range, b.source
            r.error = "the review crashed on this package (%s); it is unreviewed" % type(e).__name__
            return r

    def _one(i_b):
        i, b = i_b
        o = review.Options(**{**opt.__dict__})
        # The committer check costs GitHub calls; spend them on the first few (direct deps first).
        if i >= committer_checks and o.committers == "auto":
            o.committers = "off"
        r = engine.review(b.eco, b.name, b.new, b.old if b.change == "bumped" else None, o, change=b.change)
        r.direct, r.from_range, r.lockfile = b.direct, b.from_range, b.source
        if i >= committer_checks and opt.committers == "auto" and b.change == "bumped":
            r.unchecked.append("new-committer check skipped: past the first %d packages (--committer-checks)"
                               % committer_checks)
        if b.from_range:
            r.notes.append("the manifest names a range; its floor %s was reviewed, which may not be what installs"
                           % text.plain(b.new, 40))
        return r

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo) or 1))) as pool:
        res.reports = list(pool.map(one, enumerate(todo)))
    return res
