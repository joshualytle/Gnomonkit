"""The sync gate's engine: pinned sources, the quarantine fetch, the decision, held updates, approvals.

`gnomonkit sync` (sync.py) is the command; this module holds the parts that touch git. Standard
library only. See docs/SYNC.md for the whole picture.

Layout under ~/.gnomonkit/sync/ (GNOMONKIT_HOME moves it):

    config.toml                    the overlay approvals come from (`sync overlay set`, terminal only)
    sources.json                   each gated source: URL, branch, marketplace, and the last check's result
    approved/<source>/             the checkout Claude Code reads the marketplace from, at the pin
    quarantine/<source>.git        bare: the source's branch as last fetched; never checked out
    overlay.git                    bare: the overlay's default branch as last fetched (approvals, signers)
    signers/<source>.allowed_signers   the approved signers as last read from the overlay (a cache)
    held/<source>-<sha12>.json     an update the gate holds: the range, each reason, a file-level summary

NOTHING RUNS FROM A FETCHED TREE. Every git call here passes `core.hooksPath` pointing at a fresh empty
folder and turns off fsmonitor and submodule recursion; the quarantine is bare, so a fetch writes objects
and refs and nothing else. The approved checkout is moved with the user's git config switched off as well
(GIT_CONFIG_GLOBAL is an empty file, GIT_CONFIG_NOSYSTEM=1), so no filter, hook or helper the user
configured runs over files someone else wrote. Remotes must be https, ssh or a local path: `ext::` and
other transports that start programs are refused.

THE DECISION, for the range from the pin to the fetched head. Auto-advance when every commit is signed
by a key in the source's approved signers (`git verify-commit` with gpg.ssh.allowedSignersFile pointed at
the list) and no commit by an author or committer absent from the history before the pin touches code.
Otherwise hold. Approvals and signers come from the overlay's default branch as fetched from its remote,
never from a working tree. With no overlay the check is the signature against the suggested release key
alone, and what fails it is reported, not held.
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from . import _common as C

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
RECHECK = 3600                     # seconds: `sync check --if-due` does nothing sooner than this
LOCK_STALE = 3600
MAX_FILES = 300                    # file-level summary rows kept in a held record
MAX_PATHS = 40                     # paths listed per new contributor
SYNC_BRANCH_PREFIX = "gnomonkit-sync/"

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?$")
HELD_ID = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]{0,63})-([0-9a-f]{12}|signers)$")
SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_GITHUB_URL = re.compile(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                         r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


class GateError(C.CommandError):
    """A git step failed or a setting is unusable. The message is for a person; exit 1."""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message, code)


# ---- where things live -------------------------------------------------------------------------
def sync_dir() -> str:
    return os.path.join(C.gnomonkit_home(), "sync")


def config_path() -> str:
    return os.path.join(sync_dir(), "config.toml")


def sources_path() -> str:
    return os.path.join(sync_dir(), "sources.json")


def approved_dir(source: str | None = None) -> str:
    base = os.path.join(sync_dir(), "approved")
    return os.path.join(base, source) if source else base


def quarantine_path(source: str) -> str:
    return os.path.join(sync_dir(), "quarantine", source + ".git")


def overlay_git() -> str:
    return os.path.join(sync_dir(), "overlay.git")


def held_dir() -> str:
    return os.path.join(sync_dir(), "held")


def signers_cache(source: str) -> str:
    return os.path.join(sync_dir(), "signers", source + ".allowed_signers")


def suggested_signers_path() -> str:
    """The release key core suggests for the Gnomonkit marketplace: a setting (`[suggested] signers` in
    config.toml), defaulting to the placeholder file core ships, which holds no key until the maintainer
    adds one."""
    cfg = read_config()
    p = str(((cfg.get("suggested") or {}).get("signers") or "")).strip()
    return os.path.expanduser(p) if p else os.path.join(C.core_dir(), "sync", "suggested.allowed_signers")


SUGGESTED_FOR = "gnomonkit"        # the marketplace name the suggested release key is offered for


def check_name(name: str) -> str:
    if not isinstance(name, str) or not NAME.match(name):
        raise C.CommandError("%r is not a marketplace name (letters, digits, dot, dash, underscore)" % (name,))
    return name


# ---- git, with nothing from a fetched tree allowed to run ----------------------------------------
_TMP: dict = {}


def _cleanup(path: str) -> None:
    shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else _unlink(path)


def _unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def no_hooks_dir() -> str:
    """A fresh empty folder for core.hooksPath (made per process, so nothing can have been planted in it)."""
    d = _TMP.get("hooks")
    if not d or not os.path.isdir(d) or os.listdir(d):
        d = tempfile.mkdtemp(prefix="gnomonkit-nohooks-")
        _TMP["hooks"] = d
        atexit.register(_cleanup, d)
    return d


def empty_config() -> str:
    p = _TMP.get("config")
    if not p or not os.path.isfile(p) or os.path.getsize(p):
        fd, p = tempfile.mkstemp(prefix="gnomonkit-empty-", suffix=".gitconfig")
        os.close(fd)
        _TMP["config"] = p
        atexit.register(_cleanup, p)
    return p


_INHERITED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
              "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CONFIG", "GIT_EXEC_PATH")


def safe_options() -> list:
    return ["-c", "core.hooksPath=" + no_hooks_dir(), "-c", "core.fsmonitor=false",
            "-c", "submodule.recurse=false", "-c", "fetch.recurseSubmodules=false",
            "-c", "protocol.ext.allow=never", "-c", "log.showSignature=false", "-c", "core.quotePath=false"]


def git(args: list, git_dir: str | None = None, cwd: str | None = None, isolated: bool = False,
        input: str | None = None, env: dict | None = None, timeout: int = 180, check: bool = True,
        binary: bool = False):
    """Run git with hooks off. isolated=True also switches off the user's and the system's git config
    (for checkouts, the decision and other local steps that need none of it). binary=True leaves stdout
    as bytes. Raises GateError when check is set and git fails."""
    cmd = ["git"] + safe_options() + (["--git-dir", git_dir] if git_dir else []) + list(args)
    e = dict(os.environ)
    for k in _INHERITED:
        e.pop(k, None)
    e["GIT_TERMINAL_PROMPT"] = "0"
    e["LC_ALL"] = e.get("LC_ALL") or "C"
    if isolated:
        e["GIT_CONFIG_NOSYSTEM"] = "1"
        e["GIT_CONFIG_GLOBAL"] = empty_config()
        for k in list(e):
            if k.startswith("GIT_CONFIG_") and k not in ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL"):
                e.pop(k)
            elif k == "GIT_CONFIG_PARAMETERS":
                e.pop(k)
    e.update(env or {})
    # Bytes in and out: text-mode pipes on Windows would turn the "\n" of a blob or a message into "\r\n".
    kw = {"input": input.encode("utf-8")} if input is not None else {"stdin": subprocess.DEVNULL}
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout, env=e, creationflags=NO_WINDOW, **kw)
    except FileNotFoundError:
        raise GateError("git is not installed or not on PATH") from None
    except subprocess.TimeoutExpired:
        raise GateError("git %s timed out" % _verb(args)) from None
    r = subprocess.CompletedProcess(cmd, p.returncode,
                                    (p.stdout or b"") if binary else (p.stdout or b"").decode("utf-8", "replace"),
                                    (p.stderr or b"").decode("utf-8", "replace"))
    if check and r.returncode != 0:
        last = (r.stderr or ("" if binary else r.stdout) or "").strip().splitlines()
        raise GateError("git %s failed: %s" % (_verb(args), last[-1][:300] if last else "exit %d" % r.returncode))
    return r


def _verb(args: list) -> str:
    i = 0
    while i < len(args) and args[i] == "-c":
        i += 2
    return args[i] if i < len(args) else ""


def check_remote(url: str) -> str:
    """A remote git may fetch from: https://, ssh://, scp-like user@host:path, file:// or a local path.
    Anything that would start a program (ext::, fd::, a leading dash) is refused."""
    u = str(url or "").strip()
    if not u or u.startswith("-") or "::" in u or any(ch in u for ch in "\r\n\x00"):
        raise C.CommandError("refused remote %r: use an https://, ssh:// or local path remote" % u[:80])
    if re.match(r"(https|ssh|file)://", u, re.I) or re.match(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:", u):
        return u
    if re.match(r"[a-z][a-z0-9+.-]*://", u, re.I):
        raise C.CommandError("refused remote %r: only https, ssh and file transports" % u[:80])
    if os.path.isabs(os.path.expanduser(u)):
        return os.path.abspath(os.path.expanduser(u))
    raise C.CommandError("remote %r is neither a URL nor an absolute path" % u[:80])


def local_remote_path(url: str) -> str | None:
    """The folder a local remote names (an absolute path or a file:// URL); None for a network remote."""
    u = str(url or "").strip()
    if re.match(r"file://", u, re.I):
        p = u[7:]
        return os.path.abspath(p[1:] if re.match(r"/[A-Za-z]:[/\\]", p) else p)
    if re.match(r"(https|ssh)://", u, re.I) or re.match(r"[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:", u):
        return None
    return os.path.abspath(os.path.expanduser(u))


def is_bare_repo(path: str) -> bool:
    if not (os.path.isfile(os.path.join(path, "HEAD")) and os.path.isdir(os.path.join(path, "objects"))):
        return False
    r = git(["config", "--file", os.path.join(path, "config"), "--bool", "core.bare"], isolated=True, check=False)
    return r.returncode == 0 and (r.stdout or "").strip() == "true"


def check_overlay_remote(url: str) -> str:
    """check_remote, and a local overlay must be a bare repository. A working clone's HEAD can be any branch
    and its commits need never have been pushed, so reading approvals from it would let an unmerged commit
    approve an update (docs/SYNC.md: approvals are what was merged into the default branch)."""
    u = check_remote(url)
    p = local_remote_path(u)
    if p is not None and not is_bare_repo(p):
        raise C.CommandError("the overlay %s is a local folder but not a bare repository. Approvals must come from "
                             "what was pushed and merged, not from a working copy: give the remote it pushes to "
                             "(`git remote get-url origin` in it), or a bare repository" % u[:120])
    return u


def github_slug(remote: str) -> str | None:
    m = _GITHUB_URL.match(str(remote or "").strip())
    return m.group(1) if m else None


def init_bare(path: str) -> None:
    if os.path.isfile(os.path.join(path, "HEAD")):
        return
    C.ensure_dir(os.path.dirname(path))
    git(["init", "-q", "--bare", "--template=" + no_hooks_dir(), path], isolated=True)


def rev(git_dir: str, ref: str) -> str | None:
    r = git(["rev-parse", "--verify", "-q", ref + "^{commit}"], git_dir=git_dir, isolated=True, check=False)
    s = (r.stdout or "").strip()
    return s if r.returncode == 0 and SHA.match(s) else None


def is_ancestor(git_dir: str, a: str, b: str) -> bool:
    return git(["merge-base", "--is-ancestor", a, b], git_dir=git_dir, isolated=True, check=False).returncode == 0


def remote_head_branch(url: str) -> str | None:
    r = git(["ls-remote", "--symref", url, "HEAD"], timeout=60, check=False)
    m = re.search(r"^ref: refs/heads/(\S+)\tHEAD$", r.stdout or "", re.M)
    return m.group(1) if r.returncode == 0 and m else None


def check_branch(branch: str) -> str:
    b = str(branch or "").strip()
    if not b or b.startswith("-") or not re.match(r"[A-Za-z0-9._/-]+$", b) or ".." in b:
        raise C.CommandError("%r is not a branch name" % b[:80])
    return b


# ---- the quarantine and the approved checkout ----------------------------------------------------
def quarantine_ref(branch: str) -> str:
    return "refs/remotes/origin/" + branch


def quarantine_fetch(source: str, url: str, branch: str) -> str:
    """Fetch the source's branch into its bare quarantine. Objects and one ref, nothing else."""
    q = quarantine_path(source)
    init_bare(q)
    git(["fetch", "-q", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head", check_remote(url),
         "+refs/heads/%s:%s" % (check_branch(branch), quarantine_ref(branch))], git_dir=q, timeout=600)
    head = rev(q, quarantine_ref(branch))
    if not head:
        raise GateError("the fetch of %s left no %s branch" % (source, branch))
    return head


def approved_pin(source: str) -> str | None:
    a = approved_dir(source)
    if not os.path.isdir(os.path.join(a, ".git")):
        return None
    return rev(os.path.join(a, ".git"), "HEAD")


def _approved_git(source: str, args: list, check: bool = True):
    a = approved_dir(source)
    return git(["-C", a, "-c", "advice.detachedHead=false", "-c", "core.symlinks=false"] + args,
               isolated=True, check=check)


def approved_checkout(source: str, branch: str, sha: str) -> tuple:
    """Move the approved checkout to `sha` (creating it from the quarantine on first use) and remove every
    file git does not track there. Returns (the paths that had been changed or added on disk and were put
    back or removed, whether the pin's keep-ref was written). A served checkout is not edited."""
    a = approved_dir(source)
    if not os.path.isdir(os.path.join(a, ".git")):
        C.ensure_dir(a)
        git(["init", "-q", "--template=" + no_hooks_dir(), a], isolated=True)
    q = quarantine_path(source)
    _approved_git(source, ["fetch", "-q", "--no-tags", "--no-write-fetch-head", q,
                           "+%s:refs/remotes/quarantine/%s" % (quarantine_ref(branch), branch)])
    changed = approved_changes(source)
    _approved_git(source, ["checkout", "-q", "--force", "--detach", sha])
    _approved_git(source, ["clean", "-q", "-ffdx"])
    return changed, keep_pin(source, sha)


def keep_pin(source: str, sha: str) -> bool:
    """A ref to the pin in the quarantine, so a force-push upstream never lets gc drop the pinned commit."""
    return git(["update-ref", "refs/gnomonkit/pin", sha], git_dir=quarantine_path(source), isolated=True,
               check=False).returncode == 0


def approved_changes(source: str) -> list:
    """Paths in the approved checkout that differ from the pin: changed, added or ignored files."""
    if not approved_pin(source):
        return []
    r = _approved_git(source, ["status", "--porcelain", "-z", "--untracked-files=all", "--ignored"], check=False)
    return [x[3:] for x in (r.stdout or "").split("\0") if len(x) > 3][:50]


# ---- the overlay, as fetched ---------------------------------------------------------------------
OVERLAY_REF = "refs/remotes/overlay/default"


def read_config() -> dict:
    data = C.read_toml(config_path())
    return data if isinstance(data, dict) else {}


def overlay_config() -> dict | None:
    """{"remote", "github", "branch"} from config.toml's [overlay] table, or None when there is none."""
    o = read_config().get("overlay")
    if not isinstance(o, dict) or not str(o.get("remote") or "").strip():
        return None
    remote = str(o["remote"]).strip()
    slug = str(o.get("github") or "").strip() or github_slug(remote)
    return {"remote": remote, "github": slug if slug and SLUG.match(slug) else None,
            "branch": str(o.get("branch") or "").strip()}


def fetch_overlay(ov: dict) -> str:
    """Fetch the overlay's default branch (or the branch config names) into overlay.git. Returns its sha.
    The branch is fetched by name, never as HEAD, from a network remote or a bare repository."""
    og = overlay_git()
    remote = check_overlay_remote(ov["remote"])
    branch = ov.get("branch") or remote_head_branch(remote)
    if not branch:
        raise GateError("could not tell the overlay's default branch; name it with `gnomonkit sync overlay set "
                        "<remote> --branch <name>`, in a terminal")
    init_bare(og)
    git(["fetch", "-q", "--no-tags", "--no-recurse-submodules", "--no-write-fetch-head", remote,
         "+refs/heads/%s:%s" % (check_branch(branch), OVERLAY_REF)], git_dir=og, timeout=300)
    sha = rev(og, OVERLAY_REF)
    if not sha:
        raise GateError("the overlay's default branch could not be fetched")
    return sha


def overlay_file(path: str, ref: str = OVERLAY_REF) -> str | None:
    """A file's text on the overlay's fetched default branch; None when absent or never fetched."""
    og = overlay_git()
    if not os.path.isfile(os.path.join(og, "HEAD")):
        return None
    r = git(["cat-file", "blob", "%s:%s" % (ref, path)], git_dir=og, isolated=True, check=False)
    return r.stdout if r.returncode == 0 else None


def approvals_path() -> str:
    return "sync/approvals.toml"


def signers_path(source: str) -> str:
    return "sync/signers/%s.allowed_signers" % source


def parse_approvals(text: str | None, source: str) -> tuple:
    """([approval, ...] for this source, problem or None). An unreadable file approves nothing."""
    if not text:
        return [], None
    try:
        data = C.parse_toml(text)
    except ValueError as e:
        return [], "sync/approvals.toml on the overlay is not valid TOML (%s); it approves nothing" % e
    out = []
    items = data.get("approval") if isinstance(data, dict) else None
    for a in items if isinstance(items, list) else []:
        if not isinstance(a, dict) or a.get("source") != source:
            continue
        frm, head = str(a.get("from") or ""), str(a.get("head") or "")
        if SHA.match(frm) and SHA.match(head):
            out.append({"from": frm, "head": head, "reasons": [str(x) for x in a.get("reasons") or []
                                                                if isinstance(x, str)]})
    return out, None


def signer_lines(text: str | None) -> list:
    """The key lines of an allowed_signers text (comments and blanks dropped)."""
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip() and not ln.strip().startswith("#")]


def key_of(line: str) -> str | None:
    """The `<type> <base64>` part of an allowed_signers line, for comparing keys."""
    m = re.search(r"((?:sk-)?(?:ssh|ecdsa)-[A-Za-z0-9@.-]+)\s+([A-Za-z0-9+/=]+)", line)
    return "%s %s" % (m.group(1), m.group(2)) if m else None


# ---- the decision --------------------------------------------------------------------------------
# One field per line: no header value can hold a newline, so a name or an e-mail address can't shift a field.
_FMT = "%H%n%G?%n%an%n%ae%n%cn%n%ce%n%P"
_FIELDS = ("sha", "sig", "an", "ae", "cn", "ce", "parents")
_SIG_WHY = {"N": "unsigned", "U": "unknown-key", "E": "unverifiable", "B": "bad-signature",
            "X": "expired", "Y": "expired", "R": "revoked"}
SSH_ARMOR = "-----BEGIN SSH SIGNATURE-----"


def verify_options(signers_file: str) -> list:
    """The settings every signature check runs with, on top of isolated=True (no user or system git config,
    no GIT_CONFIG_* variables): the verdict depends on the allowed signers file alone. SSH signatures are
    checked by ssh-keygen found on PATH; the OpenPGP and X.509 programs point at a file that does not exist,
    so no keyring the user trusts can make a commit pass."""
    missing = os.path.join(no_hooks_dir(), "no-such-program")
    return ["-c", "gpg.ssh.allowedSignersFile=" + signers_file,
            "-c", "gpg.ssh.program=" + (shutil.which("ssh-keygen") or missing),
            "-c", "gpg.program=" + missing, "-c", "gpg.openpgp.program=" + missing,
            "-c", "gpg.x509.program=" + missing, "-c", "gpg.minTrustLevel=fully"]


def _cat_commits(q: str, shas: list) -> dict:
    """{sha: the raw commit object} for each sha, read in one `git cat-file --batch`."""
    r = git(["cat-file", "--batch"], git_dir=q, isolated=True, input="".join(s + "\n" for s in shas), binary=True,
            check=False)
    data, i, out = r.stdout or b"", 0, {}
    while i < len(data):
        j = data.find(b"\n", i)
        head = data[i:j].decode("ascii", "replace").split() if j >= 0 else []
        if len(head) != 3 or head[1] != "commit" or not head[2].isdigit():
            break
        n = int(head[2])
        out[head[0]] = data[j + 1:j + 1 + n]
        i = j + 1 + n + 1
    return out


def _signature_armors(raw: bytes) -> list:
    """The first line of each signature header in a raw commit (gpgsig, gpgsig-sha256)."""
    head = raw.split(b"\n\n", 1)[0].decode("utf-8", "replace")
    return [line.split(" ", 1)[1].strip() for line in head.split("\n")
            if line.split(" ", 1)[0] in ("gpgsig", "gpgsig-sha256") and " " in line]


def _unreadable(pin: str, head: str, n, files: list, total: int) -> dict:
    return {"ok": False, "commits": n, "files": files, "files_total": total,
            "reasons": [{"kind": "unverifiable", "text": "the commits from %s to %s could not be read one by one, so "
                         "none of them was checked" % (pin[:7], head[:7])}]}


def is_code(path: str) -> bool:
    """Code is anything but Markdown, docs/ and assets/. Markdown that steers an agent (skills, CLAUDE.md,
    AGENTS.md) counts as code: it changes what a session does."""
    p = path.replace("\\", "/").lstrip("/")
    if p.startswith(("docs/", "assets/")):
        return False
    if p.lower().endswith(".md"):
        base = p.rsplit("/", 1)[-1].lower()
        return "/skills/" in "/" + p or base in ("claude.md", "agents.md")
    return True


def _changed_paths(q: str, sha: str, parents: list) -> list:
    args = ["diff-tree", "-r", "-z", "--no-commit-id", "--name-only", "--no-renames"]
    args += [parents[0], sha] if parents else ["--root", sha]
    r = git(args, git_dir=q, isolated=True)
    return [p for p in (r.stdout or "").split("\0") if p]


def _file_summary(q: str, base: str, head: str) -> tuple:
    r = git(["diff", "--numstat", "-z", "--no-renames", base, head], git_dir=q, isolated=True, check=False)
    rows = []
    for item in (r.stdout or "").split("\0"):
        parts = item.split("\t", 2)
        if len(parts) == 3 and parts[2]:
            a, d = parts[0], parts[1]
            rows.append({"path": parts[2].strip("\n"), "added": int(a) if a.isdigit() else None,
                         "deleted": int(d) if d.isdigit() else None})
    return rows[:MAX_FILES], len(rows)


def known_people(q: str, pin: str) -> set:
    r = git(["log", "--format=%ae%n%ce", pin], git_dir=q, isolated=True)
    return {e.strip().lower() for e in (r.stdout or "").split("\n") if e.strip()}


def evaluate(q: str, pin: str, head: str, signers_file: str, contributors: bool = True) -> dict:
    """Judge pin..head. {"ok", "commits", "reasons": [...], "files", "files_total"}."""
    if pin == head:
        return {"ok": True, "commits": 0, "reasons": [], "files": [], "files_total": 0}
    files, total = _file_summary(q, pin, head)
    if not is_ancestor(q, pin, head):
        return {"ok": False, "commits": None, "files": files, "files_total": total,
                "reasons": [{"kind": "rewritten", "text": "the pinned commit %s is not in the fetched history: "
                             "the branch was rewritten or force-pushed" % pin[:7]}]}
    # The commits come from rev-list, and every one of them must be accounted for: a record that can't be
    # read fails the whole range instead of dropping out of the checks.
    r = git(["rev-list", "%s..%s" % (pin, head)], git_dir=q, isolated=True, check=False)
    shas = [s for s in (r.stdout or "").split("\n") if s]
    if r.returncode != 0 or not shas or not all(SHA.match(s) for s in shas):
        return _unreadable(pin, head, None, files, total)
    vo = verify_options(signers_file)
    r = git(vo + ["log", "--format=" + _FMT, "%s..%s" % (pin, head)], git_dir=q, isolated=True, check=False)
    lines = (r.stdout or "").split("\n")
    n = len(_FIELDS)
    if r.returncode != 0 or lines[-1] != "" or len(lines) - 1 != n * len(shas):
        return _unreadable(pin, head, len(shas), files, total)
    commits = []
    for i, sha in enumerate(shas):
        f = dict(zip(_FIELDS, lines[n * i:n * i + n]))
        if f["sha"] != sha:
            return _unreadable(pin, head, len(shas), files, total)
        f["parents"] = f["parents"].split()
        commits.append(f)
    raw = _cat_commits(q, shas)
    reasons = []
    for c in commits:
        armors = _signature_armors(raw[c["sha"]]) if c["sha"] in raw else None
        if armors is None:
            kind, text = "unverifiable", "commit %s could not be read"
        elif not armors:
            kind, text = "unsigned", "commit %s is unsigned"
        elif any(a != SSH_ARMOR for a in armors):
            kind, text = "unsigned", "commit %s is not signed with an SSH key (only SSH signatures are checked)"
        elif c["sig"] == "G" and git(vo + ["verify-commit", c["sha"]], git_dir=q, isolated=True,
                                     check=False).returncode == 0:
            continue
        else:
            kind = _SIG_WHY.get(c["sig"], "unverifiable") if c["sig"] != "G" else "unverifiable"
            text = {"unsigned": "commit %s is unsigned",
                    "unknown-key": "commit %s is signed by a key that is not an approved signer",
                    "bad-signature": "commit %s has a bad signature",
                    "expired": "commit %s is signed by an expired key",
                    "revoked": "commit %s is signed by a revoked key"}.get(kind, "commit %s's signature could not be "
                                                                                 "checked")
        reason = {"kind": kind, "commit": c["sha"], "text": text % c["sha"][:7]}
        key = signature_key_of(raw.get(c["sha"]) or b"") if armors else None
        if key:
            reason["key"] = fingerprint(key)
        reasons.append(reason)
    if contributors:
        known = known_people(q, pin)
        newcomers: dict = {}
        for c in reversed(commits):          # oldest first: the first commit is where a person appears
            who = [(n, e) for n, e in ((c["an"], c["ae"]), (c["cn"], c["ce"])) if e.strip().lower() not in known]
            if not who:
                continue
            code = [p for p in _changed_paths(q, c["sha"], c["parents"]) if is_code(p)]
            if not code:
                continue
            for n, e in dict.fromkeys(who):
                rec = newcomers.setdefault(e.strip().lower(), {"kind": "new-contributor", "who": "%s <%s>" % (n, e),
                                                               "commits": [], "paths": []})
                if c["sha"] not in rec["commits"]:
                    rec["commits"].append(c["sha"])
                rec["paths"] = list(dict.fromkeys(rec["paths"] + code))[:MAX_PATHS]
        for rec in newcomers.values():
            rec["text"] = "new contributor %s touched %s" % (rec["who"], ", ".join(rec["paths"][:5]) +
                                                              (" and %d more" % (len(rec["paths"]) - 5)
                                                               if len(rec["paths"]) > 5 else ""))
            reasons.append(rec)
    return {"ok": not reasons, "commits": len(commits), "reasons": reasons, "files": files, "files_total": total}


def best_approval(q: str, approvals: list, pin: str, head: str) -> dict | None:
    """The approval that moves the pin furthest towards head: its head is head or an ancestor of it, and
    its range starts at the pin or before it. An approval of exactly pin..head also releases a rewrite."""
    best, best_n = None, -1
    for a in approvals:
        if a["head"] == pin:
            continue
        if a["from"] == pin and a["head"] == head:
            return a
        if not rev(q, a["head"]) or not (a["head"] == head or is_ancestor(q, a["head"], head)):
            continue
        if not (a["from"] == pin or (rev(q, a["from"]) and is_ancestor(q, a["from"], pin))):
            continue
        if not is_ancestor(q, pin, a["head"]):
            continue
        r = git(["rev-list", "--count", "%s..%s" % (pin, a["head"])], git_dir=q, isolated=True, check=False)
        n = int((r.stdout or "0").strip() or 0)
        if n > best_n:
            best, best_n = a, n
    return best


# ---- sources, held records, and one check at a time ------------------------------------------------
def load_sources() -> dict:
    try:
        with open(sources_path(), encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    if not isinstance(d.get("sources"), dict):
        d["sources"] = {}
    d["version"] = 1
    return d


def save_sources(d: dict) -> None:
    C.atomic_write(sources_path(), json.dumps(d, indent=1, sort_keys=True) + "\n")


def held_id(source: str, head: str) -> str:
    return "%s-%s" % (source, head[:12])


def held_path(hid: str) -> str:
    return os.path.join(held_dir(), hid + ".json")


def read_held(hid: str) -> dict | None:
    if not HELD_ID.match(hid or ""):
        return None
    try:
        with open(held_path(hid), encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def list_held(source: str | None = None) -> list:
    out = []
    try:
        names = sorted(os.listdir(held_dir()))
    except OSError:
        return []
    for n in names:
        m = HELD_ID.match(n[:-5]) if n.endswith(".json") else None
        h = read_held(n[:-5]) if m else None
        # the file's name is the record's id and source; a record that says otherwise is not listed
        if h and h.get("id") == n[:-5] and h.get("source") == m.group(1) and source in (None, m.group(1)):
            out.append(h)
    return out


def clear_held(source: str, keep: str | None = None) -> None:
    """Delete this source's held records but `keep`, choosing each file by its validated name."""
    try:
        names = os.listdir(held_dir())
    except OSError:
        return
    for n in names:
        m = HELD_ID.match(n[:-5]) if n.endswith(".json") else None
        if m and m.group(1) == source and n[:-5] != keep:
            _unlink(os.path.join(held_dir(), n))


def now_iso(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t if t is not None else time.time()))


class Lock:
    """One `sync check` (or adopt, or draft) at a time. A lock older than an hour is taken over."""

    def __init__(self):
        self.path = os.path.join(sync_dir(), "check.lock")
        self.held = False

    def __enter__(self):
        C.ensure_dir(sync_dir())
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > LOCK_STALE:
                        _unlink(self.path)
                        continue
                except OSError:
                    continue
                break
        return self

    def __exit__(self, *exc):
        if self.held:
            _unlink(self.path)


# ---- one source's check ----------------------------------------------------------------------------
def signers_for(source: str, ov: dict | None) -> tuple:
    """(allowed_signers text, where it came from). Overlay: its default branch as fetched. No overlay: the
    suggested release key, for the Gnomonkit marketplace only."""
    if ov:
        return overlay_file(signers_path(source)) or "", "overlay"
    if source != SUGGESTED_FOR:
        return "", "none"
    try:
        with open(suggested_signers_path(), encoding="utf-8") as fh:
            return fh.read(), "suggested"
    except OSError:
        return "", "suggested"


# What a summary says about each kind of reason: our own words and counts, never text from upstream (names,
# e-mail addresses, paths), because summaries reach places an agent reads, such as session start.
KIND_WORDS = {"unsigned": ("unsigned commit", "unsigned commits"),
              "unknown-key": ("commit signed by a key that is not approved", "commits signed by keys that are not approved"),
              "bad-signature": ("commit with a bad signature", "commits with bad signatures"),
              "expired": ("commit signed by an expired key", "commits signed by expired keys"),
              "revoked": ("commit signed by a revoked key", "commits signed by revoked keys"),
              "unverifiable": ("commit that could not be checked", "commits that could not be checked"),
              "new-contributor": ("new contributor touching code", "new contributors touching code"),
              "rewritten": ("the branch was rewritten", "the branch was rewritten"),
              "overlay-unavailable": ("the overlay could not be fetched", "the overlay could not be fetched"),
              "other": ("other reason", "other reasons")}
_NO_COUNT = ("rewritten", "overlay-unavailable")


def kind_counts(reasons: list) -> dict:
    out: dict = {}
    for r in reasons:
        k = r.get("kind") if isinstance(r, dict) and r.get("kind") in KIND_WORDS else "other"
        out[k] = out.get(k, 0) + 1
    return out


def summary(reasons: list) -> str:
    """The reasons as kinds and counts, e.g. "2 unsigned commits, 1 new contributor touching code"."""
    parts = []
    for k, n in kind_counts(reasons).items():
        one, many = KIND_WORDS[k]
        parts.append(one if k in _NO_COUNT else "%d %s" % (n, one if n == 1 else many))
    return ", ".join(parts) or "no reason recorded"


OVERLAY_DOWN = ("the overlay could not be fetched, so its approvals and signers can't be confirmed; nothing moves "
                "until it can be")


def check_source(name: str, src: dict, ov: dict | None, overlay_note: str | None = None,
                 overlay_ok: bool = True) -> dict:
    """Fetch, decide, and advance or hold. Returns the result recorded in sources.json. With an overlay that
    could not be fetched (overlay_ok False) nothing moves: a newer upstream is held until it can be."""
    branch = src.get("branch") or "main"
    res = {"checked": now_iso(), "checked_ts": time.time()}
    try:
        head = quarantine_fetch(name, src["url"], branch)
    except C.CommandError as e:
        res.update(state="error", error=str(e)[:300], pin=approved_pin(name))
        return res
    pin = approved_pin(name)
    if not pin:
        res.update(state="error", error="no approved checkout: run `gnomonkit sync adopt %s`" % name, head=head)
        return res
    q = quarantine_path(name)
    target, steps, ev, problem, origin = pin, [], None, None, "overlay"
    if ov and not overlay_ok:
        if head != pin:
            files, total = _file_summary(q, pin, head)
            old = read_held(held_id(name, head)) or {}
            kept_reasons = [r for r in old.get("reasons") or [] if isinstance(r, dict) and
                            r.get("kind") != "overlay-unavailable"] if old.get("pin") == pin else []
            ev = {"ok": False, "commits": old.get("commits") if kept_reasons else None, "files": files,
                  "files_total": total, "reasons": [{"kind": "overlay-unavailable", "text": OVERLAY_DOWN}] + kept_reasons}
    else:
        text, origin = signers_for(name, ov)
        C.atomic_write(signers_cache(name), text)
        approvals, problem = parse_approvals(overlay_file(approvals_path()), name) if ov else ([], None)
        for _ in range(25):
            if target == head:
                break
            ev = evaluate(q, target, head, signers_cache(name), contributors=bool(ov))
            if ev["ok"]:
                steps.append({"to": head, "by": "signatures"})
                target = head
                break
            a = best_approval(q, approvals, target, head)
            if not a:
                break
            steps.append({"to": a["head"], "by": "approval"})
            target = a["head"]
    restored, kept = (approved_checkout(name, branch, target) if target != pin or approved_changes(name)
                      else ([], True))
    res.update(pin=target, head=head, previous_pin=pin, signers=origin, steps=steps)
    restored = [p for p in restored if "__pycache__" not in p.replace("\\", "/").split("/")]   # Python's own, removed quietly
    if restored:
        res["restored"] = restored
    notes = [n for n in (problem, overlay_note, None if kept else
                         "the quarantine could not keep a ref to the pin %s, so a force-push upstream could let git "
                         "drop that commit" % target[:7]) if n]
    if notes:
        res["notes"] = notes
    if target == head:
        res["state"] = "advanced" if target != pin else "current"
        clear_held(name)
        return res
    reasons = list(ev["reasons"]) if ev else []
    res["kinds"] = kind_counts(reasons)
    if not ov:
        res.update(state="unverified", summary="not offered: " + summary(reasons), reasons=reasons[:20])
        clear_held(name)
        return res
    hid = held_id(name, head)
    record = {"id": hid, "source": name, "pin": target, "head": head, "branch": branch, "url": src.get("url"),
              "commits": ev["commits"] if ev else None, "reasons": reasons, "files": ev["files"] if ev else [],
              "files_total": ev["files_total"] if ev else 0, "created": now_iso(), "signers": origin}
    old = read_held(hid)
    if old and old.get("created"):
        record["created"] = old["created"]
    C.atomic_write(held_path(hid), json.dumps(record, indent=1, sort_keys=True) + "\n")
    clear_held(name, keep=hid)
    res.update(state="held", held=hid, summary=summary(reasons))
    return res


def suggested_pending(name: str, ov: dict | None) -> list:
    """Suggested release-key lines the overlay's approved signers don't hold yet (Gnomonkit source only)."""
    if not ov or name != SUGGESTED_FOR:
        return []
    try:
        with open(suggested_signers_path(), encoding="utf-8") as fh:
            suggested = signer_lines(fh.read())
    except OSError:
        return []
    have = {key_of(ln) for ln in signer_lines(overlay_file(signers_path(name)))}
    return [ln for ln in suggested if key_of(ln) and key_of(ln) not in have]


def check_all(only: str | None = None) -> dict:
    """Run the check for every source (or one). Returns {"results": {name: result}, "overlay": ...}."""
    d = load_sources()
    ov = overlay_config()
    note, overlay_ok = None, True
    if ov:
        try:
            fetch_overlay(ov)
        except C.CommandError as e:
            overlay_ok = False
            note = "the overlay could not be fetched (%s); nothing moves until it can be" % str(e)[:400]
    results = {}
    for name, src in sorted(d["sources"].items()):
        if only and name != only:
            continue
        res = check_source(name, src, ov, note, overlay_ok)
        pend = suggested_pending(name, ov) if res.get("state") != "error" else []
        if pend:
            res["suggested_signers"] = len(pend)
        src["last"] = res
        results[name] = res
    d["checked"] = time.time()
    d["overlay"] = bool(ov)                   # for the portal's Updates tab, which never reads config.toml
    save_sources(d)
    return {"results": results, "overlay": bool(ov)}


# ---- signatures: the public key inside an SSH signature --------------------------------------------
def _ssh_string(b: bytes, i: int) -> tuple:
    n = int.from_bytes(b[i:i + 4], "big")
    return b[i + 4:i + 4 + n], i + 4 + n


def signature_key(q: str, sha: str) -> str | None:
    """`<type> <base64>` of the key that made an SSH commit signature, read from the signature itself."""
    r = git(["cat-file", "commit", sha], git_dir=q, isolated=True, check=False, binary=True)
    return signature_key_of(r.stdout or b"") if r.returncode == 0 else None


def signature_key_of(raw: bytes) -> str | None:
    """signature_key, from a raw commit object."""
    head = raw.decode("utf-8", "replace").split("\n\n", 1)[0]
    m = re.search(r"^gpgsig (-----BEGIN SSH SIGNATURE-----\n(?: .*\n)*? -----END SSH SIGNATURE-----)", head + "\n",
                  re.M)
    if not m:
        return None
    body = "".join(ln.strip() for ln in m.group(1).splitlines()[1:-1])
    try:
        raw = base64.b64decode(body)
        if raw[:6] != b"SSHSIG":
            return None
        blob, _ = _ssh_string(raw, 10)
        ktype, _ = _ssh_string(blob, 0)
        return "%s %s" % (ktype.decode("ascii"), base64.b64encode(blob).decode("ascii"))
    except (ValueError, UnicodeDecodeError, IndexError):
        return None


def fingerprint(key: str) -> str:
    blob = base64.b64decode(key.split()[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")


# ---- drafting an approval: one commit on the overlay's default branch, pushed to a sync branch -------
APPROVALS_HEADER = ("# Sync gate approvals (see Gnomonkit's docs/SYNC.md). Each [[approval]] lets the gate move a source\n"
                    "# over a range it held. `gnomonkit sync approve-draft` adds entries on a gnomonkit-sync/* branch;\n"
                    "# merging that pull request into the default branch is the approval.\n")


def signers_header(source: str) -> str:
    return ("# Approved signers for %s (see Gnomonkit's docs/SYNC.md): one allowed_signers line per key, the\n"
            "# format of ssh-keygen's ALLOWED SIGNERS. The sync gate moves %s on its own over commits signed by\n"
            "# these keys. Remove a line to stop trusting a key.\n" % (source, source))


def approval_text(existing: str | None, held: dict) -> str:
    """sync/approvals.toml with one entry appended for this held update (the rest kept as it was)."""
    t = existing if existing else APPROVALS_HEADER
    if not t.endswith("\n"):
        t += "\n"
    reasons = [r.get("text") or r.get("kind") or "" for r in held.get("reasons") or []]
    lines = ["", "[[approval]]",
             "source = %s" % C._toml_str(held["source"]),
             "from = %s" % C._toml_str(held["pin"]),
             "head = %s" % C._toml_str(held["head"]),
             "reasons = [" + ", ".join(C._toml_str(x[:300]) for x in reasons[:50]) + "]",
             "drafted = %s" % C._toml_str(now_iso())]
    return t + "\n".join(lines) + "\n"


def signers_text(existing: str | None, source: str, new_lines: list, note: str) -> str:
    t = existing if existing else signers_header(source)
    if not t.endswith("\n"):
        t += "\n"
    return t + "# %s\n" % note.replace("\n", " ")[:200] + "".join(ln + "\n" for ln in new_lines)


def overlay_base(ov: dict) -> str:
    """The overlay's default branch name: the configured one, else the remote's HEAD."""
    return ov.get("branch") or remote_head_branch(check_overlay_remote(ov["remote"])) or "main"


def remote_has_branch(remote: str, branch: str) -> bool:
    r = git(["ls-remote", "--heads", remote, "refs/heads/" + branch], timeout=60)
    return bool((r.stdout or "").strip())


def draft_commit(ov: dict, branch: str, changes: dict, message: str) -> dict:
    """Commit `changes` ({path: text}) on top of the overlay's default branch as fetched, and push the commit
    to `branch` on the overlay's remote. The overlay's working tree, wherever it is, is never touched.
    {"branch", "commit", "already"}: already is True when the branch exists there (nothing is pushed)."""
    remote = check_overlay_remote(ov["remote"])
    if remote_has_branch(remote, branch):
        return {"branch": branch, "commit": None, "already": True}
    og = overlay_git()
    base = rev(og, OVERLAY_REF)
    if not base:
        raise GateError("the overlay's default branch has not been fetched")
    fd, idx = tempfile.mkstemp(prefix="gnomonkit-index-")
    os.close(fd)
    _unlink(idx)
    env = {"GIT_INDEX_FILE": idx}
    try:
        git(["read-tree", base], git_dir=og, isolated=True, env=env)
        for path, text in sorted(changes.items()):
            blob = git(["hash-object", "-w", "--stdin"], git_dir=og, isolated=True, input=text).stdout.strip()
            git(["update-index", "--add", "--cacheinfo", "100644,%s,%s" % (blob, path)], git_dir=og, isolated=True,
                env=env)
        tree = git(["write-tree"], git_dir=og, isolated=True, env=env).stdout.strip()
    finally:
        _unlink(idx)
    commit = git(["commit-tree", tree, "-p", base, "-F", "-"], git_dir=og, input=message).stdout.strip()
    if not SHA.match(commit):
        raise GateError("git commit-tree returned no commit")
    git(["push", "-q", remote, "%s:refs/heads/%s" % (commit, branch)], git_dir=og, timeout=300)
    return {"branch": branch, "commit": commit, "already": False}
