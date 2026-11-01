#!/usr/bin/env python3
"""PreToolUse guard for the sync gate: the agent can draft an approval, never give one.

WHY. Approving an update the sync gate held is a merged pull request to the user's overlay
(docs/SYNC.md). An agent may draft that pull request (`gnomonkit sync approve-draft`); merging it is
the person's act. And the gate's own files (the approved checkout Claude Code reads plugins from, the
quarantine, the cached signers, the overlay setting) change only through `gnomonkit sync`. This hook
denies the tool calls that would cut around either:

  - Write, Edit, MultiEdit and NotebookEdit on any path under ~/.gnomonkit/sync/ (GNOMONKIT_HOME moves it);
  - a shell command that reaches that folder, unless every step of it only reads (ls, cat, git log, ...)
    with no redirect, or runs `gnomonkit sync`. Paths are resolved the way the shell would see them:
    quotes, ~ and $HOME-style variables, `.` and `..`, the call's working folder and any `cd` before;
  - `gh pr merge` on the overlay of a pull request whose head is a gnomonkit-sync/* branch or that changes
    anything under sync/ (its branch and files are looked up with `gh pr view`; a failed lookup denies).
    The repository comes from -R/--repo (attached or not, with or without a host), GH_REPO, or the
    working folder's remotes;
  - `gh api` merge calls on the overlay (pulls/N/merge, looked up the same way, merges, and the GraphQL
    mergePullRequest, enablePullRequestAutoMerge and mergeBranch mutations);
  - `git merge` or `git pull` of a gnomonkit-sync/* branch, and a push that maps one onto another branch.

CONTRACT. Fails closed on those targets and open everywhere else: a call it can't read is denied when
its text names the sync folder or a sync branch, and allowed otherwise. A denial exits 2 with the
reason on stderr. This is a guard, not a sandbox (a determined shell command can be phrased so no rule
here sees it); branch protection on the overlay, described in docs/SYNC.md, is what makes the merge the
person's own.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shlex
import subprocess
import sys

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CORE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANCH = "gnomonkit-sync/"
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
SHELL_TOOLS = ("Bash", "PowerShell")

READ_ONLY = {"ls", "dir", "cat", "type", "head", "tail", "less", "more", "stat", "file", "wc", "du",
             "grep", "rg", "egrep", "fgrep", "findstr", "find", "echo", "printf", "pwd", "cd", "pushd", "popd",
             "chdir", "test", "[", "sha256sum", "md5sum", "get-content", "gc", "get-childitem", "gci", "get-item",
             "gi", "test-path", "select-string", "sls", "resolve-path", "get-filehash", "write-output", "write-host",
             "set-location", "sl", "out-null", "cut", "jq", "diff", "cmp"}
GIT_READ_ONLY = {"log", "show", "status", "diff", "rev-parse", "cat-file", "ls-files", "ls-tree", "verify-commit",
                 "verify-tag", "for-each-ref", "describe", "shortlog", "blame", "rev-list", "merge-base", "grep",
                 "show-ref", "count-objects", "fsck", "name-rev"}
FIND_WRITES = ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fls")
GRAPHQL_MERGES = re.compile(r"\b(mergePullRequest|enablePullRequestAutoMerge|mergeBranch)\b")
CD = ("cd", "pushd", "chdir", "set-location", "sl")
ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)
GH_VALUE_FLAGS = {"-R", "--repo", "-b", "--body", "-t", "--subject", "-F", "--body-file",
                  "--match-head-commit", "-A", "--author-email", "-X", "--method", "-f", "--raw-field",
                  "--field", "-H", "--header", "--input", "-q", "--jq", "--template", "--hostname",
                  "--cache", "-p", "--preview"}


# ---- settings ----------------------------------------------------------------------------------------
def gnomonkit_home() -> str:
    raw = (os.environ.get("GNOMONKIT_HOME") or "").strip()
    return os.path.abspath(os.path.expanduser(raw)) if raw else os.path.join(os.path.expanduser("~"), ".gnomonkit")


def sync_dir() -> str:
    return os.path.join(gnomonkit_home(), "sync")


def overlay_slug() -> str | None:
    """OWNER/NAME of the overlay on GitHub, from ~/.gnomonkit/sync/config.toml; None when not set."""
    if CORE not in sys.path:
        sys.path.append(CORE)
    from gnomonkit_cmds import _common as C
    data = C.read_toml(os.path.join(sync_dir(), "config.toml")) or {}
    o = data.get("overlay") if isinstance(data.get("overlay"), dict) else {}
    slug = str(o.get("github") or "").strip()
    if not slug:
        m = re.match(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/\s]+/[^/\s]+?)(?:\.git)?/?$",
                     str(o.get("remote") or "").strip())
        slug = m.group(1) if m else ""
    return slug.lower() or None


def norm(p: str) -> str:
    p = os.path.realpath(os.path.abspath(os.path.expanduser(p)))
    return os.path.normcase(p)


def under_sync(path: str, cwd: str) -> bool:
    """True for the gate's folder and anything in it, and for any .gnomonkit/sync folder (the default place,
    whatever GNOMONKIT_HOME says), after . and .. are resolved."""
    if not path:
        return False
    p = path if os.path.isabs(os.path.expanduser(path)) else os.path.join(cwd or os.getcwd(), path)
    a, root = norm(p), norm(sync_dir())
    if a == root or a.startswith(root.rstrip(os.sep) + os.sep):
        return True
    flat = os.path.normpath(os.path.abspath(os.path.expanduser(p))).replace("\\", "/").lower()
    return re.search(r"/\.gnomonkit/sync(/|$)", flat) is not None


# ---- paths as a shell would see them ------------------------------------------------------------------
_HOME_VAR = re.compile(r"(~|\$HOME|\$\{HOME\}|\$env:HOME|\$env:USERPROFILE|\$USERPROFILE|\$\{USERPROFILE\}|"
                       r"%USERPROFILE%|%HOME%)(?=$|[\\/])", re.I)
_GK_VAR = re.compile(r"(\$GNOMONKIT_HOME|\$\{GNOMONKIT_HOME\}|\$env:GNOMONKIT_HOME|%GNOMONKIT_HOME%)(?=$|[\\/])", re.I)


def expand(token: str) -> str:
    """~, $HOME, $env:USERPROFILE, %USERPROFILE% and GNOMONKIT_HOME at the start of a path, and Git Bash's
    /c/... drive form on Windows."""
    m = _GK_VAR.match(token)
    if m:
        return gnomonkit_home() + token[m.end():]
    m = _HOME_VAR.match(token)
    if m:
        return os.path.expanduser("~") + token[m.end():]
    if os.name == "nt":
        m = re.match(r"/([A-Za-z])(?=/|$)", token)
        if m:
            return m.group(1) + ":/" + token[m.end():]
    return token


def resolve(token: str, cwd: str) -> str:
    t = expand(token)
    return t if os.path.isabs(t) else os.path.join(cwd or os.getcwd(), t)


def path_tokens(seg: str) -> list:
    """Every word of a step that could be a path, as the shell would pass it: quotes removed (both the POSIX
    reading and one that keeps backslashes, for Windows paths), redirect marks and wrapping brackets
    stripped, and the value of NAME=value or --option=value."""
    try:
        posix = shlex.split(seg, posix=True)
    except ValueError:
        posix = []
    plain = [re.sub(r"[\"']", "", w) for w in seg.split()]
    out = []
    for w in posix + plain:
        t = re.sub(r"^\d*[<>]+&?", "", w.strip("(){};"))
        if not t:
            continue
        out.append(t)
        if "=" in t:
            out.append(t.split("=", 1)[1])
    return out


def _may_glob_into_sync(p: str) -> bool:
    """A wildcard path is expanded only when the folder before its first wildcard could hold the sync folder."""
    lit = os.path.dirname(p[:min(p.find(c) for c in "*?[" if c in p)])
    root = norm(sync_dir())
    return bool(lit) and (root.startswith(norm(lit).rstrip(os.sep)) or ".gnomonkit" in lit.lower())


def reaches_sync(seg: str, cwd: str) -> bool:
    for t in path_tokens(seg):
        p = resolve(t, cwd)
        if under_sync(p, cwd):
            return True
        if any(ch in t for ch in "*?[") and _may_glob_into_sync(p):
            try:
                if any(under_sync(x, cwd) for x in glob.glob(p)[:200]):
                    return True
            except (OSError, ValueError):
                return True
    return False


def next_cwd(w: list, cwd: str) -> str:
    """The working folder after this step: a cd, pushd or Set-Location moves it."""
    if not w or os.path.basename(w[0]).lower() not in CD:
        return cwd
    args = [x for x in w[1:] if not x.startswith("-")]
    if not args:
        return os.path.expanduser("~") if os.path.basename(w[0]).lower() in ("cd", "chdir") else cwd
    return os.path.normpath(resolve(args[0], cwd))


# ---- shell commands ----------------------------------------------------------------------------------
def mentions_sync_dir(cmd: str) -> bool:
    low = cmd.lower().replace("\\\\", "/").replace("\\", "/")
    if re.search(r"\.gnomonkit/+sync(\b|/|$)", low) or re.search(r"gnomonkit_home\}?\)?/+sync(\b|/|$)", low):
        return True
    root = sync_dir().lower().replace("\\", "/")
    msys = re.sub(r"^([a-z]):/", r"/\1/", root)            # C:/x as Git Bash writes it: /c/x
    return root in low or msys in low


def segments(cmd: str) -> list:
    """The command's steps, split on ; && || | and newlines (quotes respected loosely)."""
    out, cur, q, i = [], [], None, 0
    while i < len(cmd):
        ch = cmd[i]
        if q:
            cur.append(ch)
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            cur.append(ch)
        elif ch in ";\n|&":
            if ch == "&" and ((cur and cur[-1] in "<>") or (i + 1 < len(cmd) and cmd[i + 1] == ">")):
                cur.append(ch)                    # 2>&1, &>file: a redirect, not a new step
            else:
                out.append("".join(cur))
                cur = []
                while i + 1 < len(cmd) and cmd[i + 1] in "|&":
                    i += 1
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]


def split_words(seg: str) -> tuple:
    """(the NAME=value assignments before the command, the command's words)."""
    try:
        w = shlex.split(seg, posix=True)
    except ValueError:
        w = seg.split()
    if w:
        w[0] = w[0].lstrip("({")
    env = {}
    while w:
        m = ASSIGN.match(w[0])
        if m:
            env[m.group(1).upper()] = m.group(2)
            w = w[1:]
        elif w[0] in ("sudo", "command", "env", "exec", "time", "nohup", "&"):
            w = w[1:]
        else:
            break
    return env, w


def words(seg: str) -> list:
    return split_words(seg)[1]


def shell_assignments(seg: str) -> dict:
    """Variables a step sets for the steps after it: export/set/setx NAME=value, a bare NAME=value, and
    PowerShell's $env:NAME = value."""
    out = {}
    for m in re.finditer(r"\$env:([A-Za-z_][A-Za-z0-9_]*)\s*=\s*['\"]?([^'\";\s]+)", seg, re.I):
        out[m.group(1).upper()] = m.group(2)
    env, w = split_words(seg)
    if not w:
        out.update(env)                           # NAME=value on its own
    elif w[0].lower() in ("export", "set", "declare", "typeset", "local", "readonly"):
        for x in w[1:]:
            m = ASSIGN.match(x)
            if m:
                out[m.group(1).upper()] = m.group(2)
    elif w[0].lower() == "setx" and len(w) >= 3:
        out[w[1].upper()] = w[2]
    return out


def is_gnomonkit_cli(w: list) -> bool:
    if not w:
        return False
    first = os.path.basename(w[0].replace("\\", "/")).lower()
    if first in ("gnomonkit", "gnomonkit.cmd", "gnomonkit.ps1"):
        return True
    if re.match(r"(python[0-9.]*|py)(\.exe)?$", first) and len(w) > 1:
        return os.path.basename(w[1].replace("\\", "/")).lower() == "gnomonkit.py"
    return False


def git_subcommand(w: list) -> int | None:
    """Index of git's subcommand in w (after -C DIR, --git-dir DIR and other global options)."""
    i = 1
    while i < len(w) and w[i].startswith("-"):
        i += 2 if w[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env") else 1
    return i if i < len(w) else None


def writes_by_redirect(seg: str) -> bool:
    s = re.sub(r"\d?>&\d", "", seg)
    s = re.sub(r"\d?>>?\s*(/dev/null|\$null|nul)\b", "", s, flags=re.I)
    s = re.sub(r"'[^']*'|\"[^\"]*\"", "", s)
    return ">" in s


def read_only(seg: str) -> bool:
    w = words(seg)
    if not w:
        return True
    if writes_by_redirect(seg):
        return False
    if is_gnomonkit_cli(w):
        return True
    first = os.path.basename(w[0].replace("\\", "/")).lower()
    first = first[:-4] if first.endswith(".exe") else first
    if first == "git":
        if any(x in ("-c", "--config-env") or x.startswith("--output") for x in w):
            return False                          # config on the command line can run programs; --output writes
        i = git_subcommand(w)
        return i is not None and w[i] in GIT_READ_ONLY
    if first == "find":
        return not any(x in FIND_WRITES for x in w)
    return first in READ_ONLY


def shell_reason(cmd: str, cwd: str, pr_lookup) -> str | None:
    segs = segments(cmd)
    touches, cur = mentions_sync_dir(cmd), cwd
    for seg in segs:
        touches = touches or under_sync(cur, cur) or reaches_sync(seg, cur)
        cur = next_cwd(words(seg), cur)
    if touches and not all(read_only(s) for s in segs):
        return ("the sync gate's folder (%s) changes only through `gnomonkit sync`: its approved checkout is what "
                "Claude Code loads plugins from, and its settings say where approvals come from. Reading it is fine; "
                "this command could write to it." % sync_dir())
    cur, shell_env = cwd, {}
    for seg in segs:
        r = merge_reason(seg, cur, pr_lookup, shell_env, cmd)
        if r:
            return r
        shell_env.update(shell_assignments(seg))
        cur = next_cwd(words(seg), cur)
    return None


# ---- which repository a gh command acts on --------------------------------------------------------------
def repo_flag(args: list) -> str | None:
    """The value of -R/--repo: `-R x`, `-Rx`, `--repo x`, `--repo=x`."""
    for i, x in enumerate(args):
        if x in ("-R", "--repo") and i + 1 < len(args):
            return args[i + 1]
        if x.startswith("--repo="):
            return x.split("=", 1)[1]
        if x.startswith("-R") and len(x) > 2 and not x.startswith("--"):
            return x[2:]
    return None


def norm_slug(value: str) -> str:
    """OWNER/NAME from OWNER/NAME, HOST/OWNER/NAME, a URL or an ssh remote; lower case."""
    v = str(value or "").strip().strip("\"'").lower()
    v = re.sub(r"^[a-z][a-z0-9+.-]*://", "", v)
    v = re.sub(r"^[^@/\s]+@([^:/\s]+):", r"\1/", v)
    v = re.sub(r"\.git/?$", "", v).strip("/")
    parts = [p for p in v.split("/") if p]
    if len(parts) >= 3 and "." in parts[0]:
        parts = parts[1:]
    return "/".join(parts[:2])


def cwd_slugs(cwd: str) -> set:
    """OWNER/NAME of every GitHub remote of the repository at cwd (gh may pick any of them)."""
    try:
        r = subprocess.run(["git", "-C", cwd or ".", "remote", "-v"], capture_output=True, text=True, timeout=5,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return set()
    out = set()
    for line in (r.stdout or "").splitlines() if r.returncode == 0 else []:
        parts = line.split()
        if len(parts) >= 2 and "github" in parts[1].lower():
            out.add(norm_slug(parts[1]))
    return out


def current_branch(cwd: str) -> str:
    try:
        r = subprocess.run(["git", "-C", cwd or ".", "branch", "--show-current"], capture_output=True,
                           text=True, timeout=5, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def pr_info(slug: str, selector: str | None) -> dict | None:
    """{"head", "files", "changed"} of a pull request on `slug`; None when gh can't say (the caller denies)."""
    args = ["gh", "pr", "view"] + ([selector] if selector else []) + ["--repo", slug, "--json",
                                                                      "headRefName,files,changedFiles"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
                           creationflags=NO_WINDOW)
        d = json.loads(r.stdout or "") if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    files = d.get("files")
    return {"head": str(d.get("headRefName") or ""), "changed": d.get("changedFiles"),
            "files": [str(f.get("path") or "") for f in files if isinstance(f, dict)] if isinstance(files, list)
            else None}


def approves(info: dict | None) -> bool:
    """True unless the pull request is known not to be an approval: its head is not a sync branch and every
    file it changes (all of them listed) is outside sync/."""
    if not isinstance(info, dict) or info.get("files") is None:
        return True
    files = info["files"]
    if str(info.get("head") or "").startswith(BRANCH):
        return True
    if isinstance(info.get("changed"), int) and info["changed"] > len(files):
        return True                               # more files than gh listed: can't confirm
    return any(p.replace("\\", "/").lstrip("/").lower().startswith("sync/") for p in files)


def gh_words(w: list) -> list:
    """gh's command words and positional arguments, with flags and their values left out (gh takes -R/--repo
    between `pr` and `merge` too)."""
    out, i = [], 1
    while i < len(w):
        x = w[i]
        if x in GH_VALUE_FLAGS:
            i += 2
            continue
        if not x.startswith("-"):
            out.append(x)
        i += 1
    return out


def subst(value: str, env: dict) -> str:
    """$NAME, ${NAME}, $env:NAME and %NAME% replaced from env; anything unknown is left as written."""
    def rep(m):
        name = next(g for g in m.groups() if g)
        return env.get(name.upper(), m.group(0))
    return re.sub(r"\$\{(\w+)\}|\$env:(\w+)|\$(\w+)|%(\w+)%", rep, str(value or ""), flags=re.I)


DENY_MERGE = ("merging a pull request that approves a sync update (a %s* branch, or one that changes sync/ on your "
              "overlay) is how a held plugin update gets approved, so it is the person's act, not an agent's. Draft it "
              "with `gnomonkit sync approve-draft` and ask the user to review and merge it on GitHub." % BRANCH)
DENY_UNSURE = ("this merges a pull request on your overlay, and the sync guard could not confirm that it is not a sync "
               "approval. ")


def merge_reason(seg: str, cwd: str, pr_lookup, shell_env: dict | None = None, whole: str = "") -> str | None:
    env, w = split_words(seg)
    if len(w) < 2:
        return None
    first = os.path.basename(w[0].replace("\\", "/")).lower()
    first = first[:-4] if first.endswith(".exe") else first
    if first == "git":
        i = git_subcommand(w)
        sub = w[i] if i is not None else ""
        if sub in ("merge", "pull") and any(BRANCH in x for x in w):
            return DENY_MERGE
        if sub == "push":
            for x in w:
                m = re.match(r"\+?(\S*%s\S*):(\S+)$" % re.escape(BRANCH), x)
                if m and BRANCH not in m.group(2):
                    return DENY_MERGE
        return None
    if first != "gh":
        return None
    overlay = overlay_slug()
    allenv = {k.upper(): v for k, v in os.environ.items()}
    allenv.update(shell_env or {})
    allenv.update(env)
    gw = gh_words(w)
    is_merge = gw[:2] == ["pr", "merge"]
    if is_merge and any(BRANCH in x for x in w):
        return DENY_MERGE
    text = subst(" ".join(w[1:]), allenv) if gw[:1] == ["api"] else ""
    if text:
        if GRAPHQL_MERGES.search(text) and (overlay or BRANCH in text):
            return DENY_MERGE
        if BRANCH in text and re.search(r"/merges\b|/pulls/\d+/merge\b", text):
            return DENY_MERGE
    if not overlay or not (is_merge or text):
        return None
    raw = repo_flag(w[1:]) or allenv.get("GH_REPO") or ""
    named = subst(raw, allenv)
    unknown = bool(re.search(r"[$%`]", named))
    repo = norm_slug(named) or None

    def on_overlay(r):
        return r == overlay or (r is None and overlay in cwd_slugs(cwd)) or overlay in whole.lower()

    if is_merge:
        sel = gw[2] if len(gw) > 2 else None
        m = re.match(r"https?://[^/]+/([^/]+/[^/]+)/pull/(\d+)", sel or "")
        if m:
            repo, sel, unknown = m.group(1).lower(), m.group(2), False
        if not sel:
            sel = current_branch(cwd) or None             # no selector: the pull request of the current branch
            if sel and sel.startswith(BRANCH):
                return DENY_MERGE
        if unknown or (sel and re.search(r"[$%`]", sel)):
            return DENY_UNSURE + DENY_MERGE
        if on_overlay(repo):
            return DENY_MERGE if approves(pr_lookup(overlay, sel)) else None
        return None
    if re.search(r"repos/\S*[$%`]\S*/(merges\b|pulls/\S+/merge\b)", text):
        return DENY_UNSURE + DENY_MERGE
    for m in re.finditer(r"repos/([^/\s]+/[^/\s]+)/(merges\b|pulls/(\d+)/merge\b)", text):
        slug = m.group(1).lower()
        if slug in ("{owner}/{repo}", ":owner/:repo"):
            if unknown:
                return DENY_UNSURE + DENY_MERGE
            slug = repo if repo else (overlay if overlay in cwd_slugs(cwd) else "")
        if norm_slug(slug) != overlay and overlay not in whole.lower():
            continue
        if m.group(2) == "merges":
            return DENY_MERGE
        if approves(pr_lookup(overlay, m.group(3))):
            return DENY_MERGE
    return None


# ---- the decision ------------------------------------------------------------------------------------
def decide(payload: dict, pr_lookup=None) -> str | None:
    """The reason to deny this tool call, or None to allow it."""
    tool = payload.get("tool_name")
    ti = payload.get("tool_input") or {}
    cwd = str(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    if tool in WRITE_TOOLS:
        path = str(ti.get("file_path") or ti.get("notebook_path") or "")
        if under_sync(path, cwd):
            return ("%s is under the sync gate's folder (%s), which changes only through `gnomonkit sync`: its approved "
                    "checkout is what Claude Code loads plugins from, and its settings say where approvals come from."
                    % (path, sync_dir()))
        return None
    if tool in SHELL_TOOLS:
        cmd = str(ti.get("command") or "")
        return shell_reason(cmd, cwd, pr_lookup or pr_info) if cmd.strip() else None
    return None


def main() -> int:
    raw = ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        reason = decide(payload if isinstance(payload, dict) else {})
    except Exception:
        low = raw.lower().replace("\\\\", "/").replace("\\", "/")
        if ".gnomonkit/sync" in low or BRANCH in low:
            reason = "the sync guard could not read this call, and it names the sync gate's folder or a sync branch"
        else:
            return 0
    if not reason:
        return 0
    print("BLOCKED by the sync gate's guard: %s" % reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
