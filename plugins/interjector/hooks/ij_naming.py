#!/usr/bin/env python3
"""Interjector session naming: every session titled by the naming scheme, `<repo>:<task>` by default.

SessionStart names an untitled session with the scheme's primary template and the task `new-session`.
The first real prompt then replaces that placeholder with the same template and the prompt's task, a
short slug made by plain rules (no model, no network). A title the user chose (`--name`, `/rename`, the
SDK) arrives as `session_title` and is never touched: only an absent title or this hook's own
placeholder is replaced.

Repo is the basename of the git top level of cwd. A linked worktree is named after its main repo plus
its branch (`repo@branch`), so two worktrees of one repo stay apart. Outside git: the folder name.

THE SCHEME (docs/specs/2026-10-27-session-naming-design.md). Two templates of {fields} and literal text:
`primary` (default `{repo}:{task}`) and `sub` (default `sub:{parent}/{task}`), with the fields repo,
task, role, date (local, YYYY-MM-DD), prefix and, in `sub` only, parent. They are set in the `[naming]`
table of the personal profile (`primary`, `sub`, `prefix`), and GNOMONKIT_NAMING_PRIMARY, _SUB and
_PREFIX win over the file. A scheme with any problem (check()) is not used: the defaults are, whole.
The hook, session-dispatch and the roles module all name and read titles through scheme().

A session session-dispatch opened as a VS Code tab can't be given --name: its first prompt names its brief,
and the title dispatch recorded for that brief (roles.json) is used instead of a slug.

Nothing is written to disk. The prompt is read only to build the slug, after secret redaction, or to find
a dispatched session's recorded title.
Fails open: any error means exit 0 and no output. Set GNOMONKIT_SESSION_NAMES=0 to turn it off.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

# A hook's children must never open a console window on Windows (a detached parent has none to share).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PLACEHOLDER = "new-session"
SLUG_MAX = 40
SLUG_WORDS = 6
GIT_TIMEOUT_S = 1.5

# Words that say nothing about the task. Kept short on purpose: a slug that keeps a filler word is
# fine, one that drops the subject is not.
STOP = set("""a an the and or but of to in on at for with from by into onto about as is are was were be
been being it its this that these those there here i me my we us our you your he she they them their
please pls can could would will shall should may might must do does did done let lets let's just
now then also so very really quite some any all hey hi hello ok okay thanks thank yes no not
want need like try make sure what which who how why when where if whether up out over again""".split())


def _run_git(cwd: str, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", cwd, *args], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_S,
                           creationflags=NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _branch_from_head(git_dir: str) -> str | None:
    try:
        with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as fh:
            head = fh.read().strip()
    except OSError:
        return None
    return head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") else None


def git_identity(cwd: str) -> dict | None:
    """Where cwd sits in git, from one `git rev-parse` call: top level, main repo name, whether it is
    a linked worktree, and the branch (None when detached). None outside a work tree."""
    out = _run_git(cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir",
                   "--git-dir")
    lines = (out or "").splitlines()
    if len(lines) < 3:
        return None
    top, common, git_dir = (os.path.normpath(x) for x in lines[:3])
    if os.path.basename(common).lower() == ".git":
        main = os.path.basename(os.path.dirname(common))
    else:                                            # a bare repo's worktree: "name.git"
        main = re.sub(r"\.git$", "", os.path.basename(common))
    worktree = os.path.normcase(common) != os.path.normcase(git_dir)
    return {"toplevel": top, "main": main or os.path.basename(top), "worktree": worktree,
            "branch": _branch_from_head(git_dir)}


def repo_label(cwd: str, ident: dict | None = None) -> str:
    ident = ident if ident is not None else git_identity(cwd)
    if not ident:
        return os.path.basename(os.path.normpath(cwd)) or "session"
    if ident["worktree"]:
        return f"{ident['main']}@{ident['branch'] or os.path.basename(ident['toplevel'])}"
    return os.path.basename(ident["toplevel"]) or ident["main"]


def _redact(text: str) -> str:
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ij_store
        return ij_store.redact(text)
    except Exception:
        return ""                                    # cannot redact: name from nothing rather than leak


def slug(prompt: str) -> str:
    """A short, deterministic slug of a task prompt, at most SLUG_MAX characters."""
    text = re.sub(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", " ", prompt or "", flags=re.S)
    text = re.sub(r"```.*?(```|$)", " ", text, flags=re.S)          # code blocks say little
    text = re.sub(r"\bhttps?://\S+", " ", text)
    text = _redact(text).replace("[redacted]", " ")
    words = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOP or len(w) > 20:
            continue
        if len(w) >= 8 and sum(c.isdigit() for c in w) >= 3:          # ids, hashes, numbers
            continue
        words.append(w)
        if len(words) == SLUG_WORDS:
            break
    out = ""
    for w in words:
        nxt = f"{out}-{w}" if out else w
        if len(nxt) > SLUG_MAX:
            break
        out = nxt
    return out or (words[0][:SLUG_MAX] if words else "")


# ---- the scheme ----------------------------------------------------------------------------------
PRIMARY, SUB = "primary", "sub"
DEFAULT_PRIMARY = "{repo}:{task}"
DEFAULT_SUB = "sub:{parent}/{task}"
FIELDS = ("repo", "task", "role", "date", "prefix", "parent")
FREE = ("repo", "task", "parent")                # free text: never side by side, or a title splits two ways
TEMPLATE_MAX = 80
PARENT_MAX = 40
_PREFIX = re.compile(r"[A-Za-z0-9._@#+-]{1,24}$")
_FIELD = re.compile(r"\{([^{}]*)\}")
# Sample names for the read-back check: a worktree-shaped repo, so a template that splits on / or @ shows.
SAMPLE = {"repo": "demo-repo@feat/x", "task": "fix-login", "parent": "release", "date": "2026-01-02"}


def _parts(template: str) -> list:
    """[("lit", text) | ("field", name), ...]"""
    out, i = [], 0
    for m in _FIELD.finditer(template):
        if m.start() > i:
            out.append(("lit", template[i:m.start()]))
        out.append(("field", m.group(1)))
        i = m.end()
    if i < len(template):
        out.append(("lit", template[i:]))
    return out


def parent_label(parent) -> str:
    """The `{parent}` field: whitespace and / made dashes, at most PARENT_MAX characters."""
    return re.sub(r"[\s/]+", "-", str(parent or "").strip()).strip("-")[:PARENT_MAX] or "session"


class Scheme:
    """The templates in effect, where they came from, and the problems that made the defaults apply."""

    def __init__(self, primary=DEFAULT_PRIMARY, sub=DEFAULT_SUB, prefix="", source="default", problems=()):
        self.primary, self.sub, self.prefix = primary, sub, prefix
        self.source, self.problems = source, list(problems)
        self._re = {}

    def template(self, role: str) -> str:
        return self.sub if role == SUB else self.primary

    def render(self, role: str, task: str, repo: str = "", parent: str = "", date: str | None = None) -> str:
        vals = {"repo": repo or "session", "task": task, "role": SUB if role == SUB else PRIMARY,
                "date": date or time.strftime("%Y-%m-%d"), "prefix": self.prefix, "parent": parent_label(parent)}
        return _FIELD.sub(lambda m: vals.get(m.group(1), ""), self.template(role))

    def regex(self, role: str):
        if role not in self._re:
            pat = ""
            for kind, v in _parts(self.template(role)):
                if kind == "lit":
                    pat += re.escape(v)
                elif v in ("role", "prefix"):
                    pat += re.escape(role if v == "role" else self.prefix)
                elif v == "date":
                    pat += r"(?P<date>\d{4}-\d\d-\d\d)"
                elif v == "parent":
                    pat += r"(?P<parent>.+)"          # greedy: a parent ends at the last separator, as before
                else:
                    pat += r"(?P<%s>.+?)" % v
            self._re[role] = re.compile("^" + pat + "$", re.S)
        return self._re[role]

    def match(self, name: str, role: str) -> dict | None:
        m = self.regex(role).match(name)
        return dict({k: v for k, v in m.groupdict().items() if v is not None}, role=role) if m else None


DEFAULT = Scheme()


def read(name, s: Scheme | None = None) -> dict | None:
    """A title read back into {role, repo, task, parent, date}, or None. Subs first (the scheme in effect,
    then the default one, so a title made before a change still reads as a sub), then primaries alike."""
    n = str(name or "").strip()
    if not n:
        return None
    s = s or scheme()
    for role in (SUB, PRIMARY):
        for sch in (s, DEFAULT) if s is not DEFAULT else (s,):
            r = sch.match(n, role)
            if r:
                return r
    return None


def check(primary, sub, prefix="") -> list:
    """Every reason this scheme can't be used; [] when it can."""
    problems = []
    if not isinstance(prefix, str) or (prefix and not _PREFIX.match(prefix)):
        problems.append("prefix: up to 24 of A-Z a-z 0-9 . _ @ # + -")
        prefix = ""
    for role, t in ((PRIMARY, primary), (SUB, sub)):
        if not isinstance(t, str) or not t.strip():
            problems.append("%s: empty or not text" % role)
            continue
        if len(t) > TEMPLATE_MAX:
            problems.append("%s: longer than %d characters" % (role, TEMPLATE_MAX))
        if not t.isprintable():
            problems.append("%s: holds a control character" % role)
        parts = _parts(t)
        if any(k == "lit" and ("{" in v or "}" in v) for k, v in parts):
            problems.append("%s: a { or } that is not part of a {field}" % role)
        names = [v for k, v in parts if k == "field"]
        allowed = FIELDS if role == SUB else tuple(f for f in FIELDS if f != "parent")
        bad = sorted({n for n in names if n not in allowed})
        if bad:
            problems.append("%s: unknown field %s (it may use %s)" % (
                role, ", ".join("{%s}" % b for b in bad), ", ".join("{%s}" % f for f in allowed)))
        twice = sorted({n for n in names if names.count(n) > 1})
        if twice:
            problems.append("%s: %s more than once" % (role, ", ".join("{%s}" % n for n in twice)))
        if "task" not in names:
            problems.append("%s: needs {task}, so each task gets a name of its own" % role)
        if "prefix" in names and not prefix:
            problems.append("%s: uses {prefix}, but no prefix is set" % role)
        for a, b in zip(parts, parts[1:]):
            if a[0] == b[0] == "field" and a[1] in FREE and b[1] in FREE:
                problems.append("%s: {%s}{%s} need text between them" % (role, a[1], b[1]))
    if problems:
        return problems
    s = Scheme(primary, sub, prefix)
    for role in (PRIMARY, SUB):
        fields = {k: v for k, v in SAMPLE.items() if "{%s}" % k in s.template(role)}
        name = s.render(role, SAMPLE["task"], SAMPLE["repo"], SAMPLE["parent"], SAMPLE["date"])
        got = read(name, s)
        if not got or got["role"] != role:
            problems.append("%s: a name like %s reads as a %s; make the two templates differ in their text, or use "
                            "{role}" % (role, name, got["role"] if got else "neither"))
        elif any(got.get(k) != v for k, v in fields.items()):
            problems.append("%s: a name like %s does not read back into its fields (a worktree's repo holds @ and "
                            "/, so separate the fields with other text)" % (role, name))
    return problems


# ---- where the scheme is set ---------------------------------------------------------------------
def personal_profile_path() -> str:
    """Where the core plugin keeps the personal profile, by the same order its own reader uses."""
    p = (os.environ.get("CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE") or "").strip()
    if p:
        return os.path.expanduser(p)
    import gk_grants as GR
    try:
        with open(os.path.join(GR.gnomonkit_home(), "onboarding.json"), encoding="utf-8") as fh:
            rec = json.load(fh).get("profile_option")
        if isinstance(rec, str) and rec.strip():
            return os.path.expanduser(rec.strip())
    except (OSError, ValueError, AttributeError):
        pass
    p = (os.environ.get("GNOMONKIT_PERSONAL_PROFILE") or "").strip()
    return os.path.expanduser(p) if p else os.path.join(GR.gnomonkit_home(), "personal.toml")


def profile_table(name: str, path: str | None = None) -> dict:
    """One table of the personal profile; {} when there is no profile, it can't be parsed, or no such table."""
    import gk_grants as GR
    try:
        with open(path or personal_profile_path(), encoding="utf-8") as fh:
            data = GR.parse_toml(fh.read())
    except Exception:  # noqa: BLE001 - no profile or a broken one: the defaults
        return {}
    t = data.get(name) if isinstance(data, dict) else None
    return t if isinstance(t, dict) else {}


_MEMO: dict = {}


def scheme() -> Scheme:
    """The scheme in effect: GNOMONKIT_NAMING_* over the profile's [naming] table over the defaults. One with
    problems is replaced by the defaults, whole, keeping the problems for whoever reports them."""
    env = {k: (os.environ.get("GNOMONKIT_NAMING_" + k.upper()) or "").strip() for k in ("primary", "sub", "prefix")}
    path = personal_profile_path()
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)   # size too: two edits inside one clock tick share the mtime
    except OSError:
        stamp = None
    key = (tuple(env.values()), path, stamp)
    if key in _MEMO:
        return _MEMO[key]
    table = profile_table("naming", path) if stamp is not None else {}
    vals, src, in_file = {}, [], []
    for k, default in (("primary", DEFAULT_PRIMARY), ("sub", DEFAULT_SUB), ("prefix", "")):
        if env[k]:
            vals[k] = env[k]
            src.append("GNOMONKIT_NAMING_" + k.upper())
        elif k in table:
            vals[k] = table[k]
            in_file.append(k)
        else:
            vals[k] = default
    if in_file:
        src.append("[naming] %s in %s" % (", ".join(in_file), path))
    problems = check(vals["primary"], vals["sub"], vals["prefix"]) if src else []
    s = (Scheme(source="default, because the scheme set in " + "; ".join(src) + " has problems", problems=problems)
         if problems else Scheme(vals["primary"], vals["sub"], vals["prefix"], "; ".join(src) or "default"))
    _MEMO.clear()
    _MEMO[key] = s
    return s


def is_placeholder(title: str, s: Scheme | None = None) -> bool:
    r = read(title, s)
    return bool(r and r["role"] == PRIMARY and r.get("task") == PLACEHOLDER) or title.endswith(":" + PLACEHOLDER)


def _is_real_prompt(prompt: str) -> bool:
    p = (prompt or "").lstrip()
    return bool(p) and not p.startswith(("/", "<", "!", "#"))      # slash commands, notices, shell, memory


# The first prompt session-dispatch gives a session it starts (dispatch.py FIRST_PROMPT).
_DISPATCH_PROMPT = re.compile(r"Your task brief is in (.+?\.md)\. Read it first")


def dispatched_title(prompt: str) -> str | None:
    """The title session-dispatch recorded for the brief a dispatched session's first prompt names, else None.
    A VS Code tab dispatch opened can't be given --name, so this is how it gets the title dispatch chose. Only
    a brief recorded in $GNOMONKIT_HOME/dispatch/roles.json counts, so a prompt alone can't pick a title."""
    m = _DISPATCH_PROMPT.match((prompt or "").strip())
    if not m:
        return None
    import gk_grants as GR
    try:
        with open(os.path.join(GR.gnomonkit_home(), "dispatch", "roles.json"), encoding="utf-8") as fh:
            store = json.load(fh)
    except (OSError, ValueError):
        return None
    want = os.path.normcase(os.path.normpath(m.group(1)))
    for rec in store.values() if isinstance(store, dict) else ():
        brief, name = (rec.get("brief"), rec.get("name")) if isinstance(rec, dict) else (None, None)
        if isinstance(brief, str) and isinstance(name, str) and name.strip() \
                and os.path.normcase(os.path.normpath(brief)) == want:
            return name.strip()
    return None


def _out(event: str, title: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "sessionTitle": title}}


def decide(data: dict) -> dict | None:
    if os.environ.get("GNOMONKIT_SESSION_NAMES", "1").strip().lower() in ("0", "false", "off", "no"):
        return None
    event = data.get("hook_event_name")
    title = str(data.get("session_title") or "").strip()
    s = scheme()
    if title and not is_placeholder(title, s):
        return None                                  # the user's own title (or one already set): leave it
    cwd = str(data.get("cwd") or os.getcwd())
    if event == "SessionStart":
        if title or data.get("source", "startup") not in ("startup", "resume", "fork"):
            return None
        return _out(event, s.render(PRIMARY, PLACEHOLDER, repo_label(cwd)))
    if event == "UserPromptSubmit":
        prompt = str(data.get("prompt") or "")
        chosen = dispatched_title(prompt)
        if chosen:
            return _out(event, chosen)
        task = slug(prompt) if _is_real_prompt(prompt) else ""
        if not task:
            return None                              # keep the placeholder until a prompt says something
        repo = (read(title, s) or {}).get("repo") if title else None
        return _out(event, s.render(PRIMARY, task, repo or repo_label(cwd)))
    return None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
        if isinstance(data, dict):
            out = decide(data)
            if out:
                print(json.dumps(out, ensure_ascii=False))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
