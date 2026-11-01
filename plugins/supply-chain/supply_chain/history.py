"""The new-committer check: who wrote the changes in a release, and is any of them new here?

A long-trusted project taken over by a patient newcomer looks normal at every other layer: the
same publisher, the same registry account, a release cadence like any other. What changes is
who is writing the code. So for each release this finds the source commits between the old
and the new version's tags, and for every author with no commit reachable from the OLD tag it
says what they touched: paths, and the kinds of change (install scripts, build files, network
or exec calls, obfuscated or minified blobs, binaries).

It also lists files that changed in the released ARTIFACT but are absent from the tagged
source. A build step or a hand-made tarball can carry code that no commit shows; a build file
that exists only in the artifact is flagged.

Two sources, same interface: a local clone of the upstream repository (offline, exact), or the
GitHub API (unauthenticated works for small releases; GITHUB_TOKEN or GH_TOKEN raises the rate
limit, and is only ever sent to api.github.com). Every failure is reported as "not run", never
as "no new committers".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from urllib.parse import quote

from . import artdiff, rules
from .netfetch import HTTP, FetchError

MAX_COMMITS = 500
PAGE = 100
HIGH_KINDS = frozenset({"install_script", "obfuscated", "binary"})
EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"     # git's id for an empty file
MAX_AUTHOR_CHECKS = 20
MAX_COMMIT_DETAILS = 40
# A bot is known only from identity GitHub controls: an app login ending in [bot], or the noreply
# address GitHub gives that app. Never from the free-text author name, which anyone can set to
# "renovate" to drop out of the check.
_BOT_LOGIN = re.compile(r"\[bot\]$", re.I)
_BOT_EMAIL = re.compile(r"^(?:\d+\+)?[^@\s]+\[bot\]@users\.noreply\.github\.com$", re.I)
# Files a packaging tool writes into the artifact itself; they are never in the repository.
_GENERATED = re.compile(r"(?:^|/)(?:PKG-INFO|\.cargo_vcs_info\.json|Cargo\.toml\.orig|\.cargo-ok|"
                        r"[^/]+\.egg-info/.*|[^/]+\.dist-info/.*|RECORD|WHEEL|METADATA)$")


class SourceError(RuntimeError):
    pass


@dataclass
class Commit:
    sha: str
    name: str
    email: str
    login: "str | None" = None
    files: "dict | None" = None        # path -> set of kinds from the commit's own added lines

    @property
    def key(self) -> str:
        return (self.login or self.email or self.name).lower()

    @property
    def is_bot(self) -> bool:
        return bool(_BOT_LOGIN.search(self.login or "") or _BOT_EMAIL.match(self.email or ""))

    @property
    def label(self) -> str:
        return self.login or self.name or self.email


def tag_candidates(name: str, version: str) -> list:
    short = name.split("/")[-1]
    out = []
    for c in (version, "v" + version, "%s-%s" % (short, version), "%s@%s" % (short, version),
              "%s@%s" % (name, version), "%s-v%s" % (short, version), "%s_v%s" % (short, version),
              "%s/v%s" % (short, version), "%s/%s" % (short, version), "release-%s" % version,
              "%s-%s" % (short.replace("-", "_"), version)):
        if c not in out:
            out.append(c)
    return out


def _parse_patch_kinds(patch_text: str) -> dict:
    """{path: kinds} from a `git show --unified=0` style multi-file patch."""
    files: dict = {}
    cur = None
    added: list = []
    binary = False

    def flush():
        if cur is not None:
            files[cur] = artdiff.path_kinds(cur) | artdiff.content_kinds(added, cur) | ({"binary"} if binary else set())

    for ln in patch_text.splitlines():
        if ln.startswith("diff --git "):
            flush()
            m = re.match(r"diff --git a/(.+?) b/(.+)$", ln)
            cur = m.group(2) if m else None
            added, binary = [], False
        elif ln.startswith("+++ ") or ln.startswith("--- "):
            continue
        elif ln.startswith("Binary files ") or ln.startswith("GIT binary patch"):
            binary = True
        elif ln.startswith("+") and cur is not None:
            added.append((None, ln[1:]))
    flush()
    return files


class LocalGit:
    """A local clone of the upstream repository. Read-only git plumbing; nothing is checked out."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.label = "local clone"
        if self._git("rev-parse", "--git-dir", check=False) is None:
            raise SourceError("not a git repository: pass the path of a clone of the upstream source")

    # Settings that could make git run a program: none of them may come from a config file here.
    _HARDEN = ("-c", "core.fsmonitor=", "-c", "core.pager=cat", "-c", "diff.external=", "-c", "log.showSignature=false",
               "-c", "gpg.program=false", "-c", "core.hooksPath=" + os.devnull, "-c", "core.sshCommand=false")

    def _git(self, *args, check: bool = True):
        cmd = ["git", *self._HARDEN, "-C", self.path, *args]
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   GIT_TERMINAL_PROMPT="0", GIT_PAGER="cat", GIT_EXTERNAL_DIFF="")
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120, encoding="utf-8", errors="replace", env=env,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SourceError("git failed (%s)" % type(e).__name__) from None
        if r.returncode != 0:
            if check:
                raise SourceError("git %s failed" % args[0])
            return None
        return r.stdout

    def resolve(self, name: str, version: str):
        for c in tag_candidates(name, version):
            out = self._git("rev-parse", "--verify", "--quiet", "refs/tags/%s^{commit}" % c, check=False)
            if out and out.strip():
                return out.strip(), c
        return None, None

    def commits(self, old: str, new: str):
        """([Commit], total non-merge commits in the range)."""
        total = int((self._git("rev-list", "--count", "--no-merges", "%s..%s" % (old, new)) or "0").strip() or 0)
        out = self._git("log", "--no-merges", "--format=%H%x1f%an%x1f%ae", "-n", str(MAX_COMMITS),
                        "%s..%s" % (old, new))
        res = []
        for ln in out.splitlines():
            parts = ln.split("\x1f")
            if len(parts) == 3:
                res.append(Commit(parts[0], parts[1], parts[2].lower()))
        return res, total

    def has_prior(self, c: Commit, old: str):
        if not c.email:
            return None
        # --fixed-strings: an address is matched literally. As a regex, a '|' in a crafted address
        # becomes alternation and matches some other author's history.
        out = self._git("log", "-1", "--format=%H", "--fixed-strings", "--regexp-ignore-case",
                        "--author=<%s>" % c.email, old)
        return bool(out.strip())

    def fill_files(self, c: Commit) -> None:
        out = self._git("show", "--format=", "--unified=0", "--no-color", "--no-ext-diff", "--no-textconv",
                        "--no-renames", c.sha)
        c.files = _parse_patch_kinds(out)

    def tree(self, ref: str):
        out = self._git("ls-tree", "-r", "--name-only", "--full-tree", ref)
        return set(out.splitlines()), False


class GitHubSource:
    """The GitHub REST API for one owner/name. Every call is capped and fail-soft."""

    def __init__(self, repo: str, http: HTTP) -> None:
        if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo or ""):
            raise SourceError("not a GitHub owner/name: %r" % repo)
        self.repo = repo
        self.http = http
        self.label = "GitHub %s" % repo
        self.base = "https://api.github.com/repos/%s" % repo
        self.moved = False

    _MOVED = re.compile(r"^https://api\.github\.com/repositories/(\d+)(?:[/?]|$)")

    def _get(self, path: str, headers: dict):
        """One API call. Redirects are never followed blindly (the token rides on these calls). The
        one redirect GitHub uses for a renamed or transferred repository, to the same host's
        /repositories/<id> path, is followed explicitly after its shape is checked."""
        for _attempt in range(2):
            try:
                status, body, loc = self.http.get_full(self.base + path, headers)
            except FetchError as e:
                raise SourceError(str(e)) from None
            if status not in (301, 302, 307, 308):
                return status, body
            m = self._MOVED.match(loc or "")
            if not m or "/repositories/" in self.base:
                raise SourceError("GitHub redirected an API call somewhere unexpected (HTTP %d); not followed" % status)
            self.base = "https://api.github.com/repositories/%s" % m.group(1)
            self.moved = True
        raise SourceError("GitHub kept redirecting; not followed")

    def _json(self, path: str):
        status, body = self._get(path, {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        if status in (403, 429):
            raise SourceError("GitHub rate limit or access refused (HTTP %d); set GITHUB_TOKEN, or pass a local clone"
                              % status)
        if status == 404:
            return None
        if status != 200:
            raise SourceError("GitHub HTTP %d" % status)
        try:
            return json.loads(body)
        except ValueError:
            raise SourceError("GitHub returned non-JSON") from None

    def resolve(self, name: str, version: str):
        for c in tag_candidates(name, version):
            status, body = self._get("/commits/%s" % quote(c, safe=""), {"Accept": "application/vnd.github.sha"})
            if status in (403, 429):
                raise SourceError("GitHub rate limit or access refused (HTTP %d); set GITHUB_TOKEN, or pass a local clone"
                                  % status)
            if status == 200:
                sha = body.decode("ascii", errors="replace").strip()
                if re.fullmatch(r"[0-9a-f]{40}", sha):
                    return sha, c
        return None, None

    def commits(self, old: str, new: str):
        """([Commit], total commits GitHub says the range has). Paginated up to MAX_COMMITS."""
        res, seen, total = [], 0, None
        page = 1
        while seen < MAX_COMMITS:
            d = self._json("/compare/%s...%s?per_page=%d&page=%d" % (old, new, PAGE, page)) or {}
            if total is None:
                total = d.get("total_commits")
            got = d.get("commits") or []
            for c in got:
                seen += 1
                if len(c.get("parents") or []) > 1:
                    continue
                ca = (c.get("commit") or {}).get("author") or {}
                login = (c.get("author") or {}).get("login") if isinstance(c.get("author"), dict) else None
                res.append(Commit(c.get("sha", ""), ca.get("name") or "", (ca.get("email") or "").lower(), login))
            if len(got) < PAGE or (isinstance(total, int) and seen >= total):
                break
            page += 1
        return res, (total if isinstance(total, int) else seen)

    def has_prior(self, c: Commit, old: str):
        who = c.login or c.email
        if not who:
            return None
        d = self._json("/commits?sha=%s&author=%s&per_page=1" % (old, quote(who, safe="")))
        if d is None:
            return None
        return bool(d)

    def fill_files(self, c: Commit) -> None:
        d = self._json("/commits/%s" % c.sha) or {}
        files = {}
        for f in d.get("files") or []:
            path = f.get("filename") or ""
            patch = f.get("patch")
            kinds = artdiff.path_kinds(path)
            if patch is None and f.get("status") != "removed" and not f.get("changes") \
                    and f.get("sha") != EMPTY_BLOB:
                kinds.add("binary")     # GitHub sends no patch and zero line changes for a binary file
            else:
                kinds |= artdiff.content_kinds([(None, ln[1:]) for ln in (patch or "").splitlines()
                                                if ln.startswith("+") and not ln.startswith("+++")], path)
            files[path] = kinds
        c.files = files

    def tree(self, ref: str):
        d = self._json("/git/trees/%s?recursive=1" % ref) or {}
        return {t.get("path") for t in d.get("tree") or [] if t.get("type") == "blob"}, bool(d.get("truncated"))


@dataclass
class NewAuthor:
    author: str
    commits: list = field(default_factory=list)
    files: dict = field(default_factory=dict)          # repo path -> kinds
    shipped: list = field(default_factory=list)        # artifact paths their files map to
    severity: str = "info"

    @property
    def kinds(self) -> list:
        k = set()
        for v in self.files.values():
            k |= v
        return artdiff.ordered(k)

    def as_dict(self) -> dict:
        return {"author": self.author, "commits": self.commits[:20], "commit_count": len(self.commits),
                "files": {p: artdiff.ordered(k) for p, k in sorted(self.files.items())[:60]},
                "file_count": len(self.files), "kinds": self.kinds, "shipped": sorted(self.shipped)[:60],
                "severity": self.severity}


@dataclass
class Result:
    status: str = "not run"
    source: str = ""
    old_ref: str = ""
    new_ref: str = ""
    commits: int = 0
    authors: int = 0
    new_authors: list = field(default_factory=list)
    not_in_source: list = field(default_factory=list)          # [(artifact path, kinds)]
    not_in_source_flagged: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    unchecked: list = field(default_factory=list)              # caps hit and identities that could not be checked

    @property
    def ran(self) -> bool:
        return self.status == "ran"

    def as_dict(self) -> dict:
        return {"status": self.status, "source": self.source, "old_ref": self.old_ref, "new_ref": self.new_ref,
                "commits": self.commits, "authors": self.authors,
                "new_authors": [a.as_dict() for a in self.new_authors],
                "not_in_source": [{"path": p, "kinds": artdiff.ordered(k)} for p, k in self.not_in_source[:100]],
                "not_in_source_count": len(self.not_in_source),
                "not_in_source_flagged": [{"path": p, "kinds": artdiff.ordered(k)} for p, k in self.not_in_source_flagged],
                "notes": self.notes, "unchecked": self.unchecked}


def _maps_to(repo_path: str, artifact_paths: dict):
    """The artifact path a repository path ships as: equal, or the repo path ends with it
    (a monorepo package under packages/<name>/)."""
    base = repo_path.rsplit("/", 1)[-1]
    for a in artifact_paths.get(base, ()):
        if repo_path == a or repo_path.endswith("/" + a):
            return a
    return None


def check(src, name: str, old_version: str, new_version: str, changes: list,
          old_ref: "str | None" = None, new_ref: "str | None" = None) -> Result:
    """Run the new-committer and not-in-source checks for one release."""
    res = Result(source=getattr(src, "label", ""))
    try:
        if old_ref:
            o, otag = old_ref, old_ref
        else:
            o, otag = src.resolve(name, old_version)
        if new_ref:
            n, ntag = new_ref, new_ref
        else:
            n, ntag = src.resolve(name, new_version)
        if not o or not n:
            missing = old_version if not o else new_version
            res.status = "not run: no tag found for %s (tried %s); pass --old-ref/--new-ref" % (
                missing, ", ".join(tag_candidates(name, missing)[:4]) + ", ...")
            return res
        res.old_ref, res.new_ref = otag, ntag
        commits, total = src.commits(o, n)
        res.commits = max(total, len(commits))
        if total > len(commits):
            # A release padded with hundreds of trivial commits must not push the real change off the end.
            res.unchecked.append("%d commits between the tags, %d read (cap %d): authors of the rest are unchecked"
                                 % (total, len(commits), MAX_COMMITS))
        by_author: dict = {}
        for c in commits:
            if c.is_bot:
                continue
            by_author.setdefault(c.key or ("unidentified:" + c.sha), []).append(c)
        res.authors = len(by_author)

        new_keys = []
        for i, (key, cs) in enumerate(by_author.items()):
            if i >= MAX_AUTHOR_CHECKS:
                res.unchecked.append("%d of %d authors were not checked for earlier commits (cap %d)"
                                     % (len(by_author) - MAX_AUTHOR_CHECKS, len(by_author), MAX_AUTHOR_CHECKS))
                break
            prior = src.has_prior(cs[0], o)
            if prior is None:
                # No usable identity (an empty address, no login): it cannot be shown to be known, so it
                # counts as new. Unknown must never read as trusted.
                res.unchecked.append("%s has no checkable identity; counted as new" % (cs[0].label or "an author"))
                new_keys.append(key)
            elif not prior:
                new_keys.append(key)

        art_index: dict = {}
        for fc in changes:
            art_index.setdefault(fc.path.rsplit("/", 1)[-1], []).append(fc.path)
        by_path = {fc.path: fc for fc in changes}
        details = 0
        for key in new_keys:
            cs = by_author[key]
            na = NewAuthor(cs[0].label or "(no name or address)", [c.sha[:12] for c in cs])
            for c in cs:
                if details >= MAX_COMMIT_DETAILS:
                    msg = "file lists of new authors' commits were read for the first %d only" % MAX_COMMIT_DETAILS
                    if msg not in res.unchecked:
                        res.unchecked.append(msg)
                    break
                src.fill_files(c)
                details += 1
                for p, k in (c.files or {}).items():
                    na.files.setdefault(p, set()).update(k)
            for p in list(na.files):
                a = _maps_to(p, art_index)
                if a:
                    na.shipped.append(a)
                    na.files[p] |= by_path[a].kinds
            # Docs and tests that ship (README, test.js) are not code a user runs: they are listed, not
            # flagged. A binary or obfuscated test fixture is still sensitive, whatever folder it is in.
            shipped_code = [a for a in na.shipped if not rules.non_code(a)]
            # High: what they shipped runs at install or hides what it does. A build file alone is
            # medium: most edits to one are routine, and the not-in-source check covers a build file
            # that no commit shows.
            shipped_kinds = set()
            for a in na.shipped:
                shipped_kinds |= by_path[a].kinds
            if shipped_kinds & HIGH_KINDS:
                na.severity = "high"
            elif shipped_code or set(na.kinds) & artdiff.SENSITIVE:
                na.severity = "medium"
            res.new_authors.append(na)

        tree, truncated = src.tree(n)
        if truncated:
            res.notes.append("the source tree listing was truncated; the not-in-source check may over-report")
        if tree:
            idx: dict = {}
            for p in tree:
                idx.setdefault(p.rsplit("/", 1)[-1], []).append(p)
            for fc in changes:
                if fc.status == "removed" or _GENERATED.search(fc.path):
                    continue
                cands = idx.get(fc.path.rsplit("/", 1)[-1], ())
                if any(p == fc.path or p.endswith("/" + fc.path) for p in cands):
                    continue
                res.not_in_source.append((fc.path, fc.kinds))
                if fc.kinds & {"install_script", "build_file"}:
                    res.not_in_source_flagged.append((fc.path, fc.kinds))
        if getattr(src, "moved", False):
            res.notes.append("the repository was renamed or transferred; GitHub's same-host redirect to its "
                             "repository id was followed")
        res.status = "ran"
    except SourceError as e:
        res.status = "not run: %s" % e
    return res
