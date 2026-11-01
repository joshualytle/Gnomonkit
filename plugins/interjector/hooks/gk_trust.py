"""Folder-trust levels: which folders Gnomonkit may mark trusted in Claude Code, and what Claude Code has recorded.

The levels are [[trust]] tables in the user-only grants file (~/.gnomonkit/grants.toml, GNOMONKIT_HOME moves
it), under the same guard and the same terminal rule as the grants:

    [[trust]]
    root = "<projects folder>"
    level = "trusted"           # trusted | per-project | never
    auto = true                 # trust new repositories found directly under it at session start
    owners = ["<your GitHub account>", "<your organisation>"]   # host/name for an account on another host
    local_only = false          # also trust repositories with no remote

- `trusted`: a repository root under it may be trusted (by `gnomonkit trust sync`, or at session start with
  `auto`) when its `origin` remote's owner is in `owners`, or, with `local_only`, when it has no remote. A
  bare owner name means that account on github.com; `host/name` means that account on that host.
- `per-project`: Gnomonkit trusts nothing there; the user answers Claude Code's prompt per folder.
- `never`: Gnomonkit trusts nothing there, and session-dispatch refuses to start sessions there whatever
  the grants say.
- The most specific root wins. Roots are compared as item E compares paths (gk_grants.norm).

Claude Code keys trust on the git repository root (or the folder, outside a repository), in its own file
~/.claude.json (CLAUDE_CONFIG_DIR moves it) as projects["<path>"].hasTrustDialogAccepted. A trusted parent
covers a plain subfolder but not a repository inside it: each repository needs its own entry.

This module only reads: the levels, a clone's .git/config (no git process), and Claude Code's file. Writing
that file is trust_write.py's job, in the core plugin only. Two byte-identical copies ship, in
plugins/core/hooks/ and plugins/interjector/hooks/ (session-dispatch reads the levels); a test fails when
they differ.

An owner says where a clone came from, not who wrote the code in it, and anyone who can write the folder
can change it. The levels decide which clones Gnomonkit may trust for you; they are not a review.

Standard library only, no work at import time.
"""
from __future__ import annotations

import json
import os
import re
import time

import gk_grants as G

LEVELS = ("trusted", "per-project", "never")
TRUST_KEYS = ("root", "level", "auto", "owners", "local_only")
TRUST_KEY = "hasTrustDialogAccepted"
GITHUB = "github.com"
HOST_ALIASES = {"www.github.com": GITHUB, "ssh.github.com": GITHUB}
SCAN_MAX = 5000            # folders looked at per root, at most: a scan never walks a whole disk
SKIP_DIRS = {"node_modules", "__pycache__"}
_OWNER = re.compile(r"(?:[A-Za-z0-9][A-Za-z0-9.-]*/)?[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


# ---- where things live -------------------------------------------------------------------------
def claude_json_path() -> str:
    """Claude Code's own file: $CLAUDE_CONFIG_DIR/.claude.json, else ~/.claude.json."""
    raw = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    base = os.path.abspath(os.path.expanduser(raw)) if raw else os.path.expanduser("~")
    return os.path.join(base, ".claude.json")


def trust_home() -> str:
    """~/.gnomonkit/trust: the audit log, backups of Claude Code's file, and the session-start state."""
    return os.path.join(G.gnomonkit_home(), "trust")


# ---- the levels --------------------------------------------------------------------------------
class TrustRoot:
    __slots__ = ("index", "source", "root", "key", "level", "auto", "owners", "local_only")

    def __init__(self, index, source, root, level, auto, owners, local_only):
        self.index, self.source, self.root, self.key = index, source, G.resolve(root), G.norm(root)
        self.level, self.auto, self.owners, self.local_only = level, auto, list(owners), local_only

    def covers(self, path) -> bool:
        """`path` is this root or anything under it."""
        return self.covers_norm(G.norm(path))

    def covers_norm(self, p: str) -> bool:
        """covers() for a path already normalised with gk_grants.norm."""
        return p == self.key or p.startswith(self.key.rstrip("/") + "/")

    def describe(self) -> str:
        if self.level != "trusted":
            return "level %s" % self.level
        bits = ["level trusted", "auto %s" % ("on" if self.auto else "off"),
                "owners: %s" % (", ".join(self.owners) or "none"), "local_only %s" % ("on" if self.local_only else "off")]
        return "; ".join(bits)

    def fingerprint(self) -> str:
        return json.dumps([self.key, self.level, self.auto, sorted(o.lower() for o in self.owners), self.local_only])


class Levels:
    """The [[trust]] tables, checked. status: ok | missing | unreadable | malformed | version. Only `ok`
    has roots; `missing` means no file, so no levels at all."""

    __slots__ = ("path", "status", "problems", "roots")

    def __init__(self, path, status, problems=(), roots=()):
        self.path, self.status, self.problems, self.roots = path, status, list(problems), list(roots)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _abs_root(raw) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if any(ch in s for ch in "*?["):
        return None
    t = os.path.expanduser(s)
    if not os.path.isabs(t) and not re.match(r"^[A-Za-z]:[\\/]", t):
        return None
    return t


def check_trust(data) -> tuple:
    """(status, problems, roots) for a parsed grants file. Only the `version` and the [[trust]] tables
    matter here; a problem in a [[grant]] is item E's to report."""
    if not isinstance(data, dict):
        return "malformed", ["the file is not a TOML document"], []
    if "version" not in data:
        return "malformed", ["`version = %d` is missing" % G.VERSION], []
    version = data.get("version")
    if type(version) is not int or version != G.VERSION:
        return "version", ["version %r is not one this Gnomonkit reads (it reads %d)" % (version, G.VERSION)], []
    raw = data.get("trust", [])
    if not isinstance(raw, list) or not all(isinstance(t, dict) for t in raw):
        return "malformed", ["`trust` must be [[trust]] tables"], []
    problems: list = []
    roots: list = []
    seen: dict = {}
    for n, t in enumerate(raw, 1):
        before = len(problems)
        unknown = sorted(k for k in t if k not in TRUST_KEYS)
        if unknown:
            problems.append("trust %d: unknown key %s (keys: %s)" % (n, ", ".join(unknown), ", ".join(TRUST_KEYS)))
        root = _abs_root(t.get("root"))
        if root is None:
            problems.append("trust %d: root must be an absolute folder path, without wildcards" % n)
        level = t.get("level")
        if level not in LEVELS:
            problems.append("trust %d: level must be one of %s" % (n, ", ".join(LEVELS)))
        for key in ("auto", "local_only"):
            if key in t and not isinstance(t[key], bool):
                problems.append("trust %d: %s must be true or false" % (n, key))
        owners = t.get("owners", [])
        if not isinstance(owners, list) or not all(isinstance(o, str) for o in owners):
            problems.append("trust %d: owners must be a list of account names" % n)
            owners = []
        bad = [o for o in owners if not _OWNER.match(o.strip())]
        if bad:
            problems.append("trust %d: %s %s not an account name (a name, or host/name)"
                            % (n, ", ".join(repr(o) for o in bad[:5]), "is" if len(bad) == 1 else "are"))
        if root is not None:
            key = G.norm(root)
            if key in seen:
                problems.append("trust %d: root is the same folder as trust %d" % (n, seen[key]))
            seen.setdefault(key, n)
        if len(problems) == before:
            roots.append(TrustRoot(n, t, root, level, bool(t.get("auto", False)), [o.strip() for o in owners],
                                   bool(t.get("local_only", False))))
    if problems:
        return "malformed", problems, []
    return "ok", [], roots


def load_levels(path: str | None = None) -> Levels:
    """Never raises: a file that can't be read or checked is a status with no roots."""
    path = path or G.grants_path()
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError:
        return Levels(path, "missing", ["there is no grants file yet, so no folder-trust levels"])
    except (OSError, UnicodeDecodeError) as e:
        return Levels(path, "unreadable", ["the file can't be read (%s)" % type(e).__name__])
    try:
        data = G.parse_toml(text)
    except Exception as e:  # noqa: BLE001 - any parse failure: no levels
        return Levels(path, "malformed", ["the file is not valid TOML (%s)" % str(e)[:200]])
    try:
        status, problems, roots = check_trust(data)
    except Exception as e:  # noqa: BLE001
        return Levels(path, "malformed", ["the levels could not be checked (%s)" % type(e).__name__])
    return Levels(path, status, problems, roots)


def governing(path, levels: Levels, resolved: bool = False) -> TrustRoot | None:
    """The most specific root that covers `path`, or None. `resolved`: `path` is resolved already (as the
    scan gives it), so the disk isn't asked again."""
    best = None
    p = key_norm(path) if resolved else G.norm(path)
    for r in levels.roots if levels.ok else []:
        if r.covers_norm(p) and (best is None or len(r.key) > len(best.key)):
            best = r
    return best


# ---- git, read straight from the files -----------------------------------------------------------
def git_dir(folder) -> str | None:
    """The repository's git folder when `folder` holds `.git` (a folder, or a file naming one, as worktrees
    and submodules have), else None."""
    dot = os.path.join(str(folder), ".git")
    try:
        if os.path.isdir(dot):
            return dot if os.path.exists(os.path.join(dot, "HEAD")) else None
        if os.path.isfile(dot):
            with open(dot, encoding="utf-8", errors="replace") as fh:
                first = fh.read(4096).strip()
            if first.startswith("gitdir:"):
                target = first[len("gitdir:"):].strip()
                target = target if os.path.isabs(target) else os.path.join(str(folder), target)
                return os.path.normpath(target) if os.path.isdir(target) else None
    except OSError:
        return None
    return None


def is_repo(folder) -> bool:
    return git_dir(folder) is not None


def repo_root_of(path) -> str | None:
    """The repository root holding `path` (the nearest folder at or above it with a `.git`), else None."""
    p = os.path.abspath(str(path))
    while True:
        if os.path.exists(os.path.join(p, ".git")) and is_repo(p):
            return G.resolve(p)
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent


def _config_file(gdir: str) -> str:
    """The config a worktree shares with its main repository (commondir), else the git folder's own."""
    try:
        with open(os.path.join(gdir, "commondir"), encoding="utf-8") as fh:
            common = fh.read().strip()
        if common:
            base = common if os.path.isabs(common) else os.path.join(gdir, common)
            if os.path.isfile(os.path.join(base, "config")):
                return os.path.join(os.path.normpath(base), "config")
    except OSError:
        pass
    return os.path.join(gdir, "config")


_SECTION = re.compile(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]')


def _git_value(raw: str) -> str:
    out, quoted, i = [], False, 0
    s = raw.strip()
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", "t": "\t", "b": "\b"}.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        elif ch in "#;" and not quoted:
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).strip()


def parse_git_config(text: str) -> dict:
    """{(section, subsection or None): {key: [values]}}, sections and keys lower-cased as git treats them.
    Enough of git's format for remotes: quoted values, comments, both subsection spellings."""
    conf: dict = {}
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        m = _SECTION.match(line)
        if m:
            name, sub = m.group(1).lower(), m.group(2)
            if sub is None and "." in name:            # the old [remote.origin] spelling
                name, sub = name.split(".", 1)
            elif sub is not None:
                sub = re.sub(r"\\(.)", r"\1", sub)
            cur = (name, sub)
            line = line[m.end():].strip()
            if not line or line[0] in "#;":
                continue
        if cur is None:
            continue
        key, eq, value = line.partition("=")
        key = key.strip().lower()
        if not re.match(r"^[a-z][a-z0-9-]*$", key):
            continue
        conf.setdefault(cur, {}).setdefault(key, []).append(_git_value(value) if eq else "true")
    return conf


def remotes(folder) -> dict | None:
    """{remote name: [urls]} from the repository's own config, or None when it can't be read."""
    gdir = git_dir(folder)
    if not gdir:
        return None
    try:
        with open(_config_file(gdir), encoding="utf-8", errors="replace") as fh:
            # At most 1 MiB, but asked for by the file's size: read(1 << 20) sets aside the whole MiB for
            # every small config, which adds up over thousands of repositories.
            size = os.fstat(fh.fileno()).st_size
            conf = parse_git_config(fh.read(min(size + 1, 1 << 20) if size > 0 else 1 << 20))
    except FileNotFoundError:
        return {}
    except OSError:
        return None
    out: dict = {}
    for (section, sub), keys in conf.items():
        if section == "remote" and sub is not None and keys.get("url"):
            out.setdefault(sub, []).extend(u for u in keys["url"] if u)
    return out


def parse_remote(url: str):
    """(host, owner) for a remote URL (https, ssh, git, scp-like user@host:owner/repo), or None for a local
    path, a file:// URL or anything without an owner. Credentials in the URL are dropped, never returned."""
    u = str(url or "").strip()
    if not u:
        return None
    m = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://", u)
    if m:
        if m.group(1).lower() == "file":
            return None
        rest = u[m.end():]
        authority, _, path = rest.partition("/")
        host = authority.rsplit("@", 1)[-1]
        host = host[1:host.index("]")] if host.startswith("[") and "]" in host else host.split(":", 1)[0]
    else:
        m = re.match(r"^(?:[^@/\\]+@)?([^:/\\]+):(?!//)(.+)$", u)
        if not m or len(m.group(1)) == 1:              # a Windows drive (C:\...) is a local path
            return None
        host, path = m.group(1), m.group(2)
    host = host.strip().lower()
    host = HOST_ALIASES.get(host, host)
    parts = [p for p in re.split(r"[/\\]", path.split("?", 1)[0].split("#", 1)[0]) if p]
    if not host or len(parts) < 2:
        return None
    owner = parts[0].lstrip("~")
    return (host, owner) if owner else None


def owner_listed(host: str, owner: str, owners) -> bool:
    """A bare name in `owners` is that account on github.com; host/name that account on that host. Case
    does not matter (GitHub names ignore it)."""
    h, o = str(host).lower(), str(owner).lower()
    for raw in owners or ():
        item = str(raw).strip().lower()
        if "/" in item:
            ih, _, io = item.partition("/")
            if HOST_ALIASES.get(ih, ih) == h and io == o:
                return True
        elif h == GITHUB and item == o:
            return True
    return False


def _owner_text(host: str, owner: str) -> str:
    return owner if host == GITHUB else "%s/%s" % (host, owner)


def qualifies(repo, root: TrustRoot) -> tuple:
    """(True or False, why) for a repository under a `trusted` root. The reason names the owner, never the
    remote's URL (which can hold a token)."""
    found = remotes(repo)
    if found is None:
        return False, "its git config can't be read"
    if not found:
        if root.local_only:
            return True, "no remote, and local_only is on"
        return False, "no remote (local_only = true would trust repositories with no remote)"
    urls = found.get("origin")
    if not urls:
        return False, "it has remotes but no origin"
    seen = []
    for url in urls:
        parsed = parse_remote(url)
        if parsed is None:
            return False, "origin is not a URL Gnomonkit reads an owner from"
        seen.append(parsed)
    for host, owner in seen:
        if not owner_listed(host, owner, root.owners):
            return False, "origin's owner %s is not in owners" % _owner_text(host, owner)
    host, owner = seen[0]
    return True, "origin's owner %s is listed" % _owner_text(host, owner)


# ---- scanning ----------------------------------------------------------------------------------
def find_repos(root, depth: int = 1, deadline: float | None = None) -> tuple:
    """([repository roots], complete) under `root`, from one level down to `depth` levels. A repository's
    own folders are not entered at depth 1; deeper scans look inside repositories too, since each nested
    repository is its own entry. Links that lead outside the root are skipped. `complete` is False when the
    deadline or SCAN_MAX cut the scan short."""
    base = G.resolve(root)
    nbase = G.norm(base)
    out: list = []
    looked = 0
    level = [(base, base)]            # (the folder as opened, its real path)
    for d in range(1, max(1, int(depth)) + 1):
        nxt = []
        for folder, real_folder in level:
            try:
                with os.scandir(folder) as it:
                    entries = sorted((e for e in it), key=lambda e: e.name)
            except OSError:
                continue
            for e in entries:
                if deadline is not None and time.monotonic() > deadline:
                    return out, False
                looked += 1
                if looked > SCAN_MAX:
                    return out, False
                try:
                    if not e.is_dir() or e.name == ".git":
                        continue
                except OSError:
                    continue
                if d > 1 and (e.name.startswith(".") or e.name in SKIP_DIRS):
                    continue
                if _may_lead_elsewhere(e):
                    found = G.resolve(e.path)
                    real = found.casefold() if os.name == "nt" else found
                    if not real.startswith(nbase.rstrip("/") + "/"):     # a link or junction leading elsewhere
                        continue
                else:
                    found = real_folder.rstrip("/") + "/" + e.name
                if is_repo(e.path):
                    out.append(found)
                nxt.append((e.path, found))
        level = nxt
    return out, True


_REPARSE_POINT = 0x400    # FILE_ATTRIBUTE_REPARSE_POINT: symlinks, junctions and other links on Windows


def _may_lead_elsewhere(e) -> bool:
    """A folder entry that is, or may be, a link (a symlink; on Windows also a junction or another reparse
    point), whose real path has to be asked for. An ordinary folder's real path is its parent's plus its
    name, which spares a lookup per folder (they add up over thousands)."""
    try:
        if e.is_symlink():
            return True
        if os.name == "nt":
            return bool(getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0) & _REPARSE_POINT)
    except OSError:
        return True
    return False


# ---- Claude Code's file, read only ---------------------------------------------------------------
class ClaudeConfig:
    """Claude Code's ~/.claude.json as read. `problem` is None when the format is one Gnomonkit recognises
    (a JSON object whose `projects` is an object); otherwise nothing may be written to it."""

    __slots__ = ("path", "raw", "data", "problem", "missing")

    def __init__(self, path, raw=None, data=None, problem=None, missing=False):
        self.path, self.raw, self.data, self.problem, self.missing = path, raw, data, problem, missing

    @property
    def ok(self) -> bool:
        return self.problem is None

    @property
    def projects(self) -> dict:
        return self.data.get("projects") if self.ok else {}


def parse_claude_config(raw: bytes):
    """(data, problem): the parsed file, or None and what is wrong with it."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "it is not UTF-8 text"
    if text.startswith("\ufeff"):
        return None, "it starts with a byte-order mark"
    try:
        data = json.loads(text)
    except ValueError as e:
        return None, "it is not valid JSON (%s)" % str(e)[:120]
    if not isinstance(data, dict):
        return None, "it is not a JSON object"
    if "projects" not in data:
        return None, "it has no `projects` object"
    if not isinstance(data["projects"], dict):
        return None, "its `projects` is not an object"
    return data, None


def read_claude_config(path: str | None = None) -> ClaudeConfig:
    """Never raises."""
    path = path or claude_json_path()
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return ClaudeConfig(path, problem="there is no %s yet (Claude Code writes it once it has run)"
                            % os.path.basename(path), missing=True)
    except OSError as e:
        return ClaudeConfig(path, problem="it can't be read (%s)" % type(e).__name__)
    data, problem = parse_claude_config(raw)
    return ClaudeConfig(path, raw, data, problem)


def key_norm(k) -> str:
    """A projects key as compared: one separator, no trailing slash, case-folded on Windows."""
    p = str(k).replace("\\", "/")
    if len(p) > 1 and p.endswith("/") and not re.match(r"^[A-Za-z]:/$", p):
        p = p.rstrip("/")
    return p.casefold() if os.name == "nt" else p


def _forms(folder, resolved: bool = False) -> set:
    if resolved:
        return {key_norm(folder)}
    return {key_norm(G.resolve(folder)), key_norm(os.path.abspath(str(folder)))}


def key_index(projects: dict) -> dict:
    """{key_norm(key): [keys]} over `projects`, built once so looking up many folders doesn't walk every
    entry for each one."""
    out: dict = {}
    for k in projects or {}:
        out.setdefault(key_norm(k), []).append(k)
    return out


def entry_keys(projects: dict, folder, index: dict | None = None, resolved: bool = False) -> list:
    """The keys in `projects` that name `folder` (in any spelling of separators and, on Windows, case).
    `index` is key_index(projects), to reuse across lookups. `resolved`: `folder` is already a resolved
    path, so only that spelling counts (nothing on disk is looked at, so a link made since can't redirect
    the lookup)."""
    idx = index if index is not None else key_index(projects)
    out: list = []
    for f in _forms(folder, resolved):
        out.extend(idx.get(f, ()))
    return out


def is_trusted(projects: dict, folder, index: dict | None = None, resolved: bool = False) -> bool:
    """`folder`'s own entry says the trust prompt was accepted. Exact: a trusted parent does not count."""
    return any(isinstance(projects.get(k), dict) and projects[k].get(TRUST_KEY) is True
               for k in entry_keys(projects, folder, index, resolved))


def trust_state(folder, cfg: ClaudeConfig | None = None):
    """True when Claude Code would treat `folder` as trusted, False when not, None when its file can't be
    read. Inside a repository only the repository root's own entry counts; outside one, the folder's or a
    parent's, up to the first folder holding a `.git` (one git can't read still answers for itself). A hint:
    the file is Claude Code's, not a contract."""
    cfg = cfg if cfg is not None else read_claude_config()
    if not cfg.ok:
        return None
    projects = cfg.projects
    index = key_index(projects)
    root = repo_root_of(folder)
    if root:
        return is_trusted(projects, root, index)
    p = G.resolve(folder)
    while True:
        if is_trusted(projects, p, index):
            return True
        if os.path.exists(os.path.join(p, ".git")):
            return False
        parent = G._parent(p)
        if parent == p:
            return False
        p = parent


class Candidate:
    """A repository found under a `trusted` root: whether Claude Code trusts it now, whether it qualifies
    for Gnomonkit to trust, and why."""

    __slots__ = ("repo", "root", "trusted", "qualifies", "reason")

    def __init__(self, repo, root, trusted, ok, reason):
        self.repo, self.root, self.trusted, self.qualifies, self.reason = repo, root, trusted, ok, reason

    @property
    def pending(self) -> bool:
        return self.qualifies and not self.trusted


def candidates(levels: Levels, projects: dict, depth: int = 1, auto_only: bool = False,
               deadline: float | None = None, roots=None, pending_only: bool = False) -> tuple:
    """([Candidate], complete) for every repository under the `trusted` roots (only those with `auto` when
    `auto_only`), each judged by the most specific root that covers it: one under a `per-project` or
    `never` root inside a trusted one is left out. `pending_only` leaves out the ones already trusted
    without reading their config. `complete` is False when the deadline or SCAN_MAX cut it short."""
    out: list = []
    seen: set = set()
    complete = True
    index = key_index(projects)
    for r in roots if roots is not None else levels.roots:
        if r.level != "trusted" or (auto_only and not r.auto) or not os.path.isdir(r.root):
            continue
        repos, done = find_repos(r.root, depth, deadline)
        complete = complete and done
        for repo in repos:
            if deadline is not None and time.monotonic() > deadline:
                return out, False
            k = key_norm(repo)              # find_repos resolved it
            if k in seen:
                continue
            seen.add(k)
            gov = governing(repo, levels, resolved=True)
            if gov is None or gov.level != "trusted" or (auto_only and not gov.auto):
                continue
            trusted = is_trusted(projects, repo, index, resolved=True)
            if pending_only and trusted:
                continue
            ok, why = qualifies(repo, gov)
            out.append(Candidate(repo, gov, trusted, ok, why))
    return out, complete
