"""gnomonkit overlay: make, convert, check and connect your private overlay.

    gnomonkit overlay init DIR [--name NAME] [--github OWNER/NAME]
    gnomonkit overlay convert REPO [--name NAME] [--apply]
    gnomonkit overlay check [DIR]
    gnomonkit overlay link [DIR]

An overlay is a private git repository that is also a Claude Code marketplace: your own skills and
hooks, your personal profile, your leakcheck denylist and the evidence from your own use, in one
layout (docs/OVERLAY.md). `init` makes one in an empty or missing folder from a template of
synthetic examples, runs `git init`, and commits only when your git identity is set. `convert`
prints the plan for bringing an existing repository into the layout and, with --apply, copies into
it: it never moves, overwrites or deletes, so the originals stay until you remove them. `check`
validates the layout: exit 0, 1 with the problems listed, 2 when the folder is not an overlay.
`link` prints the commands that connect it and runs none of them.

Nothing here creates a remote, pushes, or runs `gh repo create`: those are yours to run. The commands
printed for them always make a PRIVATE repository, and there is no option for a public one. A
repository that is public on GitHub (checked with `gh` when it is installed) is refused.

Other modules find the overlay with find_overlay(path) and configured_overlay().
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess

from . import _common as C

FORMAT = 1
MARKER = "gnomonkit-overlay.toml"
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_overlay_template")
PLUGIN_NAME = "overlay"
PLUGIN_VERSION = "0.1.0"

# Where each part lives in format 1. The marker's `parts` lists the ones in use.
PARTS = {
    "plugin": "plugins/overlay",
    "profile": "profile/personal.toml",
    "lessons": "lessons/register.json",
    "denylist": "private/gnomonkit-denylist.txt",
    "evidence": "evidence",
}
MARKETPLACE = ".claude-plugin/marketplace.json"
PLUGIN_MANIFEST = "plugins/overlay/.claude-plugin/plugin.json"
# Files that must be committed: an ignore rule over any of them is a mistake `check` reports.
MUST_TRACK = ("private/gnomonkit-denylist.txt", "profile/personal.toml", "lessons/register.json",
              "evidence/README.md")
OVERLAY_ENTRY = {"name": PLUGIN_NAME, "source": "./plugins/overlay",
                 "description": "Private skills and hooks for one person's setup."}
INIT_MESSAGE = "Gnomonkit overlay, made by gnomonkit overlay init"

MARKER_HEADER = ("Gnomonkit overlay marker. This repository holds private rules and data: keep it private.\n"
                 "Written by `gnomonkit overlay`; `gnomonkit overlay check` validates the layout.\n"
                 "Format 1 is described in Gnomonkit's docs/OVERLAY.md.")
PROFILE_HEADER = (
    "Gnomonkit personal profile, kept in this private overlay. See Gnomonkit's docs/PROFILES.md.\n"
    "Every setting below is commented out, so Gnomonkit's defaults apply. To change one, run\n"
    "    gnomonkit profile set --file profile/personal.toml key=value\n"
    "which checks the value, rewrites this file without the comments, and keeps the old one as\n"
    "personal.toml.bak. Point core's personal_profile setting at this file: `gnomonkit overlay link`\n"
    "prints how.")

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*$")
_SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}$")
_GH_URL = re.compile(r"^(?:https?://(?:[^@/]+@)?github\.com/|(?:ssh://)?git@github\.com[:/])"
                     r"([A-Za-z0-9-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", re.I)
# Agent-instruction files from other tools: rules in them are a human decision, never copied blindly.
AGENT_FILES = ("AGENTS.md", "GEMINI.md", ".cursorrules", ".windsurfrules", ".github/copilot-instructions.md",
               ".cursor/rules")
_RULE_NAME = re.compile(r"rule|guideline|convention|lesson|playbook|standard|preference|style|policy|principle|"
                        r"checklist|instruction|how-?to", re.I)
_RULE_DIRS = {"rules", "guidelines", "conventions", "lessons", "playbooks", "standards", "instructions", "prompts"}
_NOT_RULES = {"readme.md", "changelog.md", "license.md", "contributing.md", "security.md", "code_of_conduct.md",
              "claude.md", "agents.md", "gemini.md"}
_HEAVY = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox", ".mypy_cache",
          ".pytest_cache", ".ruff_cache", "target", ".next", "vendor"}
MAX_WALK = 5000


# ---- where an overlay is -------------------------------------------------------------------------
def find_overlay(start: str, levels: int = 4) -> str | None:
    """The overlay folder holding `start` (a file or folder), looking up at most `levels` folders for
    the marker; None when there is none."""
    try:
        p = os.path.abspath(os.path.expanduser(start))
        if not os.path.isdir(p):
            p = os.path.dirname(p)
        for _ in range(levels + 1):
            if os.path.isfile(os.path.join(p, MARKER)):
                return p
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
    except (OSError, ValueError, TypeError):
        pass
    return None


def configured_overlay() -> str | None:
    """The overlay the personal profile lives in (core's personal_profile setting pointed into one), or
    None when the profile is at the default location or outside any overlay. Never raises."""
    try:
        path, source = C.personal_profile_path()
        if source == "default location":
            return None
        return find_overlay(os.path.dirname(path))
    except Exception:  # noqa: BLE001 - callers include hooks
        return None


def _p(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


def read_marker(root: str) -> tuple:
    """(data, error): the marker's tables, or None and why it can't be read."""
    path = os.path.join(root, MARKER)
    if not os.path.isfile(path):
        return None, "no %s" % MARKER
    try:
        data = C.load_toml(path)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return None, "%s doesn't parse (%s)" % (MARKER, e)
    return data, None


# ---- names ---------------------------------------------------------------------------------------
def name_problem(name: str) -> str | None:
    """None when `name` can be the overlay's marketplace name, else why not."""
    if not isinstance(name, str) or not _NAME.match(name) or len(name) > 64:
        return ("the name must be lower-case letters, digits and single hyphens, up to 64 characters "
                "(for example my-overlay)")
    if name == "gnomonkit":
        return "gnomonkit is the shared marketplace's name; pick another one for your overlay"
    if name.startswith(("claude", "anthropic")):
        return "Claude Code reserves marketplace names that look official; pick another one"
    return None


def suggest_name(folder: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", os.path.basename(os.path.normpath(folder)).lower()).strip("-")[:64].strip("-")
    return s if s and name_problem(s) is None else "my-overlay"


def github_slug(url: str) -> str | None:
    m = _GH_URL.match((url or "").strip())
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


# ---- running git and gh --------------------------------------------------------------------------
def _forbidden(argv) -> bool:
    """True for anything that would create a remote or publish: gh repo create, git push, git remote
    add / set-url. This module never runs those; the guard makes that a property of the code path."""
    if not argv:
        return False
    tool = os.path.splitext(os.path.basename(str(argv[0])))[0].lower()
    args = [str(a) for a in argv[1:]]
    if tool == "gh":
        words = [a for a in args if not a.startswith("-")]
        return words[:2] == ["repo", "create"]
    if tool == "git":
        i = 0
        while i < len(args) and args[i].startswith("-"):
            i += 2 if args[i] in ("-C", "-c") else 1
        sub = args[i:i + 2]
        return bool(sub) and (sub[0] == "push" or (sub[0] == "remote" and sub[1:2] in (["add"], ["set-url"])))
    return False


def _run(argv, cwd=None, timeout=30) -> tuple:
    """(exit code or None, stdout, stderr). Never raises for a missing or failing program."""
    if _forbidden(argv):
        raise RuntimeError("refused to run %s: creating a remote and pushing are yours to do" % " ".join(argv[:3]))
    try:
        r = subprocess.run([str(a) for a in argv], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return None, "", str(e)
    return r.returncode, r.stdout or "", r.stderr or ""


def _git(root: str, *args, timeout=30) -> tuple:
    git = shutil.which("git")
    if not git:
        return None, "", "git is not installed"
    return _run([git, "-C", root] + list(args), timeout=timeout)


def repo_top(folder: str) -> str | None:
    """The top of the git repository holding `folder` (the nearest existing folder up), or None."""
    p = os.path.abspath(folder)
    while not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    rc, out, _ = _git(p, "rev-parse", "--show-toplevel")
    return C.resolve_path(out.strip()) if rc == 0 and out.strip() else None


def _is_repo_root(root: str) -> bool:
    top = repo_top(root)
    return bool(top) and C.normalise_path(top) == C.normalise_path(root)


def remotes(root: str) -> list:
    """[(name, url)] for the repository at `root`."""
    rc, out, _ = _git(root, "config", "--get-regexp", r"^remote\..*\.url$")
    found = []
    for line in out.splitlines() if rc == 0 else ():
        key, _, url = line.partition(" ")
        if key.startswith("remote.") and key.endswith(".url"):
            found.append((key[len("remote."):-len(".url")], url.strip()))
    return found


def github_visibility(slug: str) -> str | None:
    """"public", "private" or "internal" as GitHub reports it through `gh`; None when that can't be
    told (gh not installed or not signed in, no access, or the repository doesn't exist yet)."""
    gh = shutil.which("gh")
    if not gh:
        return None
    rc, out, _ = _run([gh, "repo", "view", slug, "--json", "visibility", "--jq", ".visibility"], timeout=20)
    v = out.strip().lower() if rc == 0 else ""
    return v if v in ("public", "private", "internal") else None


def visibility_report(root: str, extra_slugs=()) -> tuple:
    """(ok_lines, problems, notes, slugs) for every GitHub repository this overlay is tied to: the
    marker's `github` and every GitHub remote."""
    slugs, ok, problems, notes = [], [], [], []
    for s in extra_slugs:
        if s and s not in slugs:
            slugs.append(s)
    others = []
    for name, url in remotes(root):
        s = github_slug(url)
        if s and s not in slugs:
            slugs.append(s)
        elif not s:
            others.append(name)
    for s in slugs:
        vis = github_visibility(s)
        if vis == "private":
            ok.append("%s is private on GitHub" % s)
        elif vis == "public":
            problems.append("%s is PUBLIC on GitHub. Make it private now (gh repo edit %s --visibility private "
                            "--accept-visibility-change-consequences) and treat what it held as exposed."
                            % (s, s))
        elif vis == "internal":
            problems.append("%s is internal on GitHub, so everyone in its enterprise can read it; make it private "
                            "(gh repo edit %s --visibility private --accept-visibility-change-consequences)" % (s, s))
        else:
            notes.append("couldn't tell whether %s is private (gh isn't installed or signed in, or the repository "
                         "doesn't exist yet): check it yourself before you push" % s)
    for name in others:
        notes.append("remote %s is not on GitHub, so its visibility wasn't checked: make sure it is private" % name)
    return ok, problems, notes, slugs


# ---- what the files say --------------------------------------------------------------------------
def parse_denylist(text: str) -> tuple:
    """(rule count, problems) for a denylist, read as leakcheck reads it: `category<TAB>regex` per line,
    # comments. Problems name the line, never its text: the text is what must not leak."""
    rules, problems = 0, []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" not in line:
            problems.append("denylist line %d: expected category<TAB>regex (there is no TAB)" % n)
            continue
        _cat, rx = line.split("\t", 1)
        try:
            compiled = re.compile(rx.strip(), re.I)
        except re.error:
            problems.append("denylist line %d: the regular expression doesn't compile" % n)
            continue
        if compiled.search(""):
            problems.append("denylist line %d: the regular expression matches empty text, so it would flag "
                            "every line of every file" % n)
            continue
        rules += 1
    return rules, problems


def skill_problem(path: str) -> str | None:
    """None when SKILL.md has front matter with a non-empty `description:`, else what is missing."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read(20000).replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return "can't be read as UTF-8 text"
    if not text.startswith("---"):
        return "has no front matter (the file must start with ---)"
    end = text.find("\n---", 3)
    if end < 0:
        return "its front matter is not closed with ---"
    front = text[3:end].split("\n")
    for i, line in enumerate(front):
        if line.startswith("description:"):
            v = line[len("description:"):].strip()
            if v in (">", "|", ">-", "|-", ">+", "|+", ""):
                nxt = front[i + 1] if i + 1 < len(front) else ""
                v = nxt.strip() if nxt[:1] in (" ", "\t") else ""
            if v in ("", '""', "''"):
                return "has an empty description: (it is what says when the skill applies)"
            return None
    return "has no description: (it is what says when the skill applies)"


def _read_json(path: str) -> tuple:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh), None
    except OSError as e:
        return None, "can't be read (%s)" % (e.strerror or e)
    except ValueError as e:
        return None, "is not valid JSON (%s)" % e


def _lists_overlay(market: dict) -> bool:
    for entry in market.get("plugins") or ():
        src = entry.get("source") if isinstance(entry, dict) else None
        if isinstance(src, str) and src.strip().rstrip("/").lstrip("./") == "plugins/overlay":
            return True
    return False


def profile_template() -> str:
    """A personal profile with every setting commented out, from the one schema, so it never drifts."""
    examples = {"text": "example", "int": 0, "number": 0, "repo": "example-org/my-overlay", "bool": False}
    out = [("# " + line).rstrip() for line in PROFILE_HEADER.splitlines()]
    for table, names in C.personal_layout():
        settings = [C.SCHEMA_BY_KEY["%s.%s" % (table, n)] for n in names]
        settings = [s for s in settings if s.group != "record"]
        if not settings:
            continue
        out += ["", "# [%s]" % table]
        for s in settings:
            value = s.default
            if value is None:
                value = s.choices[0] if s.choices else examples.get(s.kind, "example")
            entry = "# " + C.dumps({s.name: value}).strip()
            hint = " | ".join(s.choices) if s.choices else s.label
            if s.default is None:
                hint += " (not set by default)"
            out.append("%-46s # %s" % (entry, hint))
    return "\n".join(out) + "\n"


def marker_text(name: str, parts, github: str | None = None) -> str:
    data = {"format": FORMAT, "name": name}
    if github:
        data["github"] = github
    data["parts"] = [p for p in PARTS if p in set(parts)]
    return C.dumps(data, [("", ["format", "name", "github", "parts"])], MARKER_HEADER)


def marketplace_text(name: str) -> str:
    data = {"name": name, "owner": {"name": name},
            "metadata": {"description": "A private Gnomonkit overlay. Keep this repository private."},
            "plugins": [OVERLAY_ENTRY]}
    return json.dumps(data, indent=2) + "\n"


def plugin_text(name: str) -> str:
    data = {"name": PLUGIN_NAME, "description": "Private skills and hooks from the %s overlay." % name,
            "version": PLUGIN_VERSION}
    return json.dumps(data, indent=2) + "\n"


def template_files(name: str, github: str | None = None) -> dict:
    """{relative path: text} for a new overlay, in the order they are written."""
    files = {MARKER: marker_text(name, ("plugin", "profile", "denylist", "evidence"), github)}
    for folder, _dirs, names in sorted(os.walk(TEMPLATE_DIR)):
        for fn in sorted(names):
            src = os.path.join(folder, fn)
            rel = os.path.relpath(src, TEMPLATE_DIR).replace(os.sep, "/")
            if rel == "gitignore":
                rel = ".gitignore"   # stored without the dot: in Gnomonkit's tree it would ignore files there
            with open(src, encoding="utf-8") as fh:
                files[rel] = fh.read().replace("{{name}}", name)
    files[MARKETPLACE] = marketplace_text(name)
    files[PLUGIN_MANIFEST] = plugin_text(name)
    files[PARTS["profile"]] = profile_template()
    return files


def _write_new(root: str, rel: str, data, mode: int = 0o600) -> bool:
    """Write a file that doesn't exist yet; False (and nothing written) when something is already there."""
    path = _p(root, rel)
    if os.path.lexists(path):
        return False
    C.atomic_write(path, data, mode)
    return True


def _q(path: str) -> str:
    """A path quoted for the shell the person is likely to paste it into."""
    if os.name == "nt":
        return '"%s"' % path
    return shlex.quote(path)


def _rel(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


# ---- init ----------------------------------------------------------------------------------------
def _only_empty_repo(target: str) -> bool:
    """True when the folder holds nothing but a .git with no commits (an empty clone or a fresh git init)."""
    if os.listdir(target) != [".git"]:
        return False
    rc, _, _ = _git(target, "rev-parse", "--verify", "-q", "HEAD")
    return rc not in (0, None)


def _branch(root: str) -> str:
    rc, out, _ = _git(root, "symbolic-ref", "--short", "HEAD")
    return out.strip() if rc == 0 and out.strip() else "main"


def remote_steps(root: str, github: str | None, committed: bool) -> list:
    branch = _branch(root)
    later = "" if committed else "   (after your first commit)"
    if remotes(root):
        return ["Push it yourself when you are ready (nothing here pushes); the remote must be PRIVATE:",
                "  git -C %s push -u origin %s%s" % (_q(root), branch, later)]
    lines = ["Make the remote yourself: nothing here creates one or pushes. It must be PRIVATE."]
    if github:
        lines.append("  gh repo create %s --private --source %s --remote origin" % (github, _q(root)))
    else:
        lines.append("  gh repo create <owner>/<name> --private --source %s --remote origin" % _q(root))
        lines.append("  (or make a private repository on your git host, then: git -C %s remote add origin <its url>)"
                     % _q(root))
    lines.append("  git -C %s push -u origin %s%s" % (_q(root), branch, later))
    return lines


def cmd_init(args) -> int:
    target = C.resolve_path(args.dir)
    if os.path.exists(target) and not os.path.isdir(target):
        raise C.CommandError("%s is a file, not a folder" % target)
    if os.path.dirname(target) == target or C.normalise_path(target) == C.normalise_path(os.path.expanduser("~")):
        raise C.CommandError("refused: an overlay gets a folder of its own, not a drive root or the home folder")
    existing_repo = False
    if os.path.isdir(target) and os.listdir(target):
        if not _only_empty_repo(target):
            raise C.CommandError(
                "refused: %s is not empty. init makes a new overlay only in an empty or missing folder. To bring "
                "an existing repository into the format, run: %s" % (target, C.command_line("overlay", "convert", _q(target))))
        existing_repo = True
    name = args.name or suggest_name(target)
    problem = name_problem(name)
    if problem:
        raise C.CommandError("%s (got %r); pass --name" % (problem, name))
    github = args.github
    if github and not _SLUG.match(github):
        raise C.CommandError("--github must look like owner/name")
    if existing_repo:
        _ok, public, _notes, _ = visibility_report(target, [github] if github else [])
        if public:
            raise C.CommandError("refused: " + public[0])
    outer = None if existing_repo else repo_top(target)

    files = template_files(name, github)
    C.ensure_dir(target)
    for rel, text in files.items():
        _write_new(target, rel, text)
    print("Made the overlay %r in %s:" % (name, target))
    for rel in files:
        print("  " + rel)

    committed = False
    if not shutil.which("git"):
        print("\ngit isn't installed, so this folder is not a repository yet: run `git init` there, then commit.")
    else:
        if not existing_repo:
            rc, _, err = _git(target, "init", "-q")
            if rc != 0:
                print("\n`git init` failed (%s); run it yourself, then commit." % err.strip())
        who = [_git(target, "config", "--get", k)[1].strip() for k in ("user.name", "user.email")]
        if all(who):
            _git(target, "add", "-A")
            rc, _, err = _git(target, "commit", "-q", "-m", INIT_MESSAGE, timeout=120)
            committed = rc == 0
            print("\n" + ("Made the first commit." if committed else
                          "The first commit failed (%s). Commit yourself: git -C %s commit -m \"%s\""
                          % (err.strip().splitlines()[-1] if err.strip() else "no reason given", _q(target),
                             INIT_MESSAGE)))
        else:
            print("\nNo commit: your git identity isn't set. Set it (git config --global user.name ... and "
                  "user.email, a no-reply address if you use one), then: git -C %s add -A && git -C %s commit -m "
                  "\"%s\"" % (_q(target), _q(target), INIT_MESSAGE))
    if outer:
        print("\nNote: this folder is inside the git repository at %s. The overlay is a repository of its own; "
              "add the folder to that repository's .gitignore, or move the overlay out." % outer)
    print("")
    for line in remote_steps(target, github, committed):
        print(line)
    print("\nNext:\n  %s      checks the layout\n  %s       prints the commands that connect it"
          % (C.command_line("overlay", "check", _q(target)), C.command_line("overlay", "link", _q(target))))
    return 0


# ---- convert -------------------------------------------------------------------------------------
class Plan:
    def __init__(self, root: str):
        self.root = root
        self.name = None
        self.github = None
        self.copies = []      # (label, [(src_rel, dst_rel), ...])
        self.creates = {}     # dst_rel -> text
        self.kept = []        # (rel, why)
        self.decisions = []   # text
        self.notes = []       # text
        self.parts = set()
        self.originals = []   # src folders and files that stay where they are


def _walk(root: str) -> list:
    """Relative paths of files under root, four folders deep at most, skipping caches and dependencies."""
    out = []
    for folder, dirs, files in os.walk(root):
        rel_folder = _rel(root, folder)
        depth = 0 if rel_folder == "." else rel_folder.count("/") + 1
        dirs[:] = sorted(d for d in dirs if depth < 4 and d not in _HEAVY
                         and not os.path.islink(os.path.join(folder, d)))
        for fn in sorted(files):
            out.append(fn if rel_folder == "." else rel_folder + "/" + fn)
            if len(out) >= MAX_WALK:
                return out
    return out


def _same(a: str, b: str) -> bool:
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def _plan_market(plan: Plan, name_arg: str | None) -> None:
    root = plan.root
    path = _p(root, MARKETPLACE)
    if not os.path.isfile(path):
        plan.name = name_arg or suggest_name(root)
        problem = name_problem(plan.name)
        if problem:
            raise C.CommandError("%s (got %r); pass --name" % (problem, plan.name))
        plan.creates[MARKETPLACE] = marketplace_text(plan.name)
        return
    data, err = _read_json(path)
    if err or not isinstance(data, dict):
        plan.name = name_arg or suggest_name(root)
        plan.kept.append((MARKETPLACE, "left alone"))
        plan.decisions.append("%s %s: fix it, and list plugins/overlay in it, so overlay@%s installs"
                              % (MARKETPLACE, err or "is not a JSON object", plan.name))
        return
    if data.get("name") == "gnomonkit":
        raise C.CommandError("refused: this is Gnomonkit's own repository (its marketplace is named gnomonkit). "
                             "An overlay is a separate, private repository: make one with overlay init.")
    have = data.get("name") if isinstance(data.get("name"), str) else None
    plan.name = have or name_arg or suggest_name(root)
    if have and name_arg and name_arg != have:
        plan.decisions.append("the marketplace is already named %r, so the overlay keeps that name (not %r)"
                              % (have, name_arg))
    if have and name_problem(have):
        plan.decisions.append("the marketplace name %r: %s" % (have, name_problem(have)))
    if _lists_overlay(data):
        plan.kept.append((MARKETPLACE, "already lists plugins/overlay"))
    else:
        plan.kept.append((MARKETPLACE, "left alone: an existing manifest is never rewritten"))
        plan.decisions.append("add this entry to the plugins list in %s, so overlay@%s installs: %s"
                              % (MARKETPLACE, plan.name, json.dumps(OVERLAY_ENTRY)))


def _plan_skills(plan: Plan) -> None:
    root = plan.root
    src_root = _p(root, ".claude/skills")
    if not os.path.isdir(src_root):
        return
    for skill in sorted(os.listdir(src_root)):
        sdir = os.path.join(src_root, skill)
        if not os.path.isdir(sdir) or skill.startswith("."):
            continue
        pairs = []
        for folder, dirs, files in os.walk(sdir):
            dirs[:] = sorted(d for d in dirs if d not in _HEAVY)
            for fn in sorted(files):
                src = os.path.join(folder, fn)
                src_rel = _rel(root, src)
                dst_rel = "plugins/overlay/skills/%s/%s" % (skill, _rel(sdir, src))
                dst = _p(root, dst_rel)
                if os.path.islink(src):
                    plan.decisions.append("%s is a symbolic link: copy what it points at yourself" % src_rel)
                elif os.path.lexists(dst):
                    if not _same(src, dst):
                        plan.decisions.append("%s already exists and differs from %s: both left as they are; "
                                              "merge them by hand" % (dst_rel, src_rel))
                else:
                    pairs.append((src_rel, dst_rel))
        skill_md = os.path.join(sdir, "SKILL.md")
        if not os.path.isfile(skill_md):
            plan.decisions.append(".claude/skills/%s has no SKILL.md, so it isn't a skill yet" % skill)
        else:
            problem = skill_problem(skill_md)
            if problem:
                plan.decisions.append(".claude/skills/%s/SKILL.md %s; `check` reports it until it is fixed"
                                      % (skill, problem))
        if pairs:
            plan.copies.append((".claude/skills/%s/ -> plugins/overlay/skills/%s/" % (skill, skill), pairs))
            plan.originals.append(".claude/skills/%s/" % skill)
    if plan.copies:
        plan.notes.append("once overlay@%s is installed the copied skills load in every project; in this repository "
                          "the originals under .claude/skills/ load too until you remove them" % plan.name)


def _plan_agent_files(plan: Plan, files: list) -> None:
    root = plan.root
    if os.path.isfile(_p(root, "CLAUDE.md")):
        plan.kept.append(("CLAUDE.md", "already where an overlay keeps it"))
        plan.decisions.append("CLAUDE.md is read only when an agent works inside this repository. Rules in it that "
                              "should follow you to every project belong in a skill under plugins/overlay/skills/")
    for rel in AGENT_FILES:
        if os.path.exists(_p(root, rel)):
            plan.kept.append((rel, "left alone"))
            plan.decisions.append("%s holds instructions for agents. Rules in it that should apply everywhere belong "
                                  "in a skill under plugins/overlay/skills/" % rel)
    settings, err = (None, None)
    if os.path.isfile(_p(root, ".claude/settings.json")):
        settings, err = _read_json(_p(root, ".claude/settings.json"))
    hooks = settings.get("hooks") if isinstance(settings, dict) else None
    if isinstance(hooks, dict) and hooks:
        count = sum(len(g.get("hooks") or []) for groups in hooks.values() if isinstance(groups, list)
                    for g in groups if isinstance(g, dict))
        plan.decisions.append(
            ".claude/settings.json has %d hook%s (%s). They run only in this repository. To run one everywhere, "
            "copy its script to plugins/overlay/hooks/ and list it in plugins/overlay/hooks/hooks.json with "
            "${CLAUDE_PLUGIN_ROOT} paths; a plugin hook runs in every session, so decide one at a time"
            % (count, "" if count == 1 else "s", ", ".join(sorted(hooks))))
    elif err:
        plan.decisions.append(".claude/settings.json %s; its hooks weren't read" % err)
    for kind in ("commands", "agents"):
        if os.path.isdir(_p(root, ".claude/" + kind)):
            plan.decisions.append(".claude/%s/: a plugin can carry these too (plugins/overlay/%s/), where they are "
                                  "named overlay:<name>; copy the ones you want everywhere" % (kind, kind))
    rules = []
    for rel in files:
        parts = rel.split("/")
        fn = parts[-1]
        if not fn.lower().endswith(".md") or fn.lower() in _NOT_RULES or parts[0] in (".claude", ".github", ".cursor"):
            continue
        if _RULE_NAME.search(fn[:-3]) or any(p.lower() in _RULE_DIRS for p in parts[:-1]):
            rules.append(rel)
    for rel in rules[:20]:
        plan.decisions.append("%s reads like rules: turn the ones that apply everywhere into a skill under "
                              "plugins/overlay/skills/, or leave it as it is" % rel)
    if len(rules) > 20:
        plan.decisions.append("%d more markdown files read like rules (the first 20 are listed)" % (len(rules) - 20))


def _plan_single(plan: Plan, part: str, candidates: list, template: str, check) -> None:
    """The profile or the denylist: keep it in place, copy the one found elsewhere, or make the template."""
    rel = PARTS[part]
    root = plan.root
    if os.path.isfile(_p(root, rel)):
        plan.kept.append((rel, "already in place"))
        plan.parts.add(part)
        for problem in check(_p(root, rel)):
            plan.decisions.append("%s: %s" % (rel, problem))
        return
    if len(candidates) > 1:
        plan.decisions.append("several files could be your %s (%s): copy the one you use to %s yourself"
                              % (part, ", ".join(candidates), rel))
        return
    if candidates:
        plan.copies.append(("%s -> %s" % (candidates[0], rel), [(candidates[0], rel)]))
        plan.originals.append(candidates[0])
        plan.parts.add(part)
        for problem in check(_p(root, candidates[0])):
            plan.decisions.append("%s: %s" % (candidates[0], problem))
        return
    plan.creates[rel] = template
    plan.parts.add(part)


def _profile_check(path: str) -> list:
    try:
        data = C.load_toml(path)
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return ["doesn't parse as TOML (%s)" % e]
    return C.validate_profile(data)


def _denylist_check(path: str) -> list:
    try:
        with open(path, encoding="utf-8-sig") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        return ["can't be read (%s)" % e]
    _rules, problems = parse_denylist(text)
    return problems


def build_plan(root: str, name_arg: str | None = None) -> Plan:
    plan = Plan(root)
    _plan_market(plan, name_arg)
    template = template_files(plan.name)
    if not os.path.isfile(_p(root, PLUGIN_MANIFEST)):
        plan.creates[PLUGIN_MANIFEST] = template[PLUGIN_MANIFEST]
    else:
        plan.kept.append((PLUGIN_MANIFEST, "already in place"))
    plan.parts.update(("plugin", "evidence"))
    _plan_skills(plan)
    files = _walk(root)
    _plan_agent_files(plan, [f for f in files if f.split("/")[0] not in ("plugins", "evidence", "private", "profile",
                                                                          "lessons")])
    tmpl_denylist = PARTS["denylist"]
    profiles = [f for f in files if f.split("/")[-1] == "personal.toml" and f != PARTS["profile"]
                and not f.startswith("plugins/")]
    denylists = [f for f in files if re.search(r"deny[-_]?list", f.split("/")[-1], re.I)
                 and os.path.splitext(f)[1].lower() in ("", ".txt") and f != tmpl_denylist]
    _plan_single(plan, "profile", profiles, template[PARTS["profile"]], _profile_check)
    _plan_single(plan, "denylist", denylists, template[tmpl_denylist], _denylist_check)
    if os.path.isfile(_p(root, PARTS["lessons"])):
        plan.kept.append((PARTS["lessons"], "already in place"))
        plan.parts.add("lessons")
    for rel in ("evidence/README.md", "README.md", "CLAUDE.md", ".gitignore"):
        if rel == "CLAUDE.md" and os.path.isfile(_p(root, rel)):
            continue   # listed with the agent files
        if os.path.lexists(_p(root, rel)):
            why = "make sure it says this repository must stay private" if rel == "README.md" else "left alone"
            plan.kept.append((rel, why))
        else:
            plan.creates[rel] = template[rel]
    is_repo = _is_repo_root(root)
    if is_repo and os.path.isfile(_p(root, ".gitignore")):
        for problem in ignored_by_mistake(root):
            plan.decisions.append(problem)
    if not is_repo:
        plan.notes.append("this folder is not a git repository yet: --apply runs `git init` here (nothing is "
                          "committed; you review and commit)")
    origin = dict(remotes(root)).get("origin") if is_repo else None
    plan.github = github_slug(origin) if origin else None
    plan.creates[MARKER] = marker_text(plan.name, plan.parts, plan.github)
    return plan


def _print_plan(plan: Plan) -> None:
    print("Plan for making %s the Gnomonkit overlay %r:" % (plan.root, plan.name))
    if plan.copies:
        print("\nCopied into the overlay layout (the originals stay where they are):")
        for label, pairs in plan.copies:
            print("  %s%s" % (label, "   (%d files)" % len(pairs) if len(pairs) > 1 else ""))
    if plan.creates:
        print("\nMade from the template:")
        for rel in sorted(plan.creates, key=lambda r: (r != MARKER, r)):
            print("  " + rel)
    if plan.kept:
        print("\nLeft as they are:")
        for rel, why in plan.kept:
            print("  %-36s %s" % (rel, why))
    if plan.decisions:
        print("\nNeeds your decision:")
        for d in plan.decisions:
            print("  - " + d)
    if plan.notes:
        print("\nNotes:")
        for n in plan.notes:
            print("  - " + n)


def cmd_convert(args) -> int:
    root = C.resolve_path(args.repo)
    if not os.path.isdir(root):
        raise C.CommandError("%s is not a folder" % root)
    if os.path.isfile(os.path.join(root, MARKER)):
        raise C.CommandError("%s is already an overlay: run %s" % (root, C.command_line("overlay", "check", _q(root))))
    top = repo_top(root)
    if top and C.normalise_path(top) != C.normalise_path(root):
        raise C.CommandError("refused: %s is inside the repository at %s. Convert that repository, or move this "
                             "folder out of it first." % (root, top))
    ok, public, vnotes, _slugs = visibility_report(root) if top else ([], [], [], [])
    if public:
        raise C.CommandError("refused: %s An overlay holds private rules and identifiers, so it can't be a public "
                             "repository. Make a new private one with overlay init and copy what you need." % public[0])
    plan = build_plan(root, args.name)
    plan.notes = ok + vnotes + plan.notes
    _print_plan(plan)
    if not args.apply:
        print("\nDry run: nothing was changed. To make these copies (nothing is moved, overwritten or deleted):\n  %s"
              % C.command_line("overlay", "convert", _q(root), "--apply"))
        return 0

    written, skipped = [], []
    for _label, pairs in plan.copies:
        for src_rel, dst_rel in pairs:
            src = _p(root, src_rel)
            with open(src, "rb") as fh:
                data = fh.read()
            mode = (os.stat(src).st_mode & 0o700) | 0o600
            (written if _write_new(root, dst_rel, data, mode) else skipped).append(dst_rel)
    for rel in sorted(plan.creates, key=lambda r: (r == MARKER, r)):   # the marker last: it says "done"
        (written if _write_new(root, rel, plan.creates[rel]) else skipped).append(rel)
    if not top and shutil.which("git"):
        rc, _, err = _git(root, "init", "-q")
        print("\n" + ("Ran git init." if rc == 0 else "`git init` failed (%s): run it yourself." % err.strip()))
    print("\nWrote %d file%s:" % (len(written), "" if len(written) == 1 else "s"))
    for rel in written:
        print("  " + rel)
    for rel in skipped:
        print("  (left alone, something was already there: %s)" % rel)
    if plan.originals:
        print("\nThe originals are still in place: %s. Remove them yourself once the overlay works."
              % ", ".join(plan.originals))
    print("\nNext: review the changes and commit them yourself (nothing was committed):\n  git -C %s status\n"
          "  %s      checks the layout\n  %s       prints the commands that connect it"
          % (_q(root), C.command_line("overlay", "check", _q(root)), C.command_line("overlay", "link", _q(root))))
    if not plan.github:
        print("")
        for line in remote_steps(root, None, False):
            print(line)
    return 0


# ---- check ---------------------------------------------------------------------------------------
def ignored_by_mistake(root: str) -> list:
    """Problems for each must-commit path an ignore rule would leave out (git check-ignore --no-index)."""
    out = []
    for rel in MUST_TRACK:
        rc, stdout, _ = _git(root, "check-ignore", "--no-index", "-v", "--", rel)
        if rc == 0:
            # <source>:<line>:<pattern>TAB<path>; the source may itself hold a drive letter's colon
            m = re.match(r"(.*?):(\d+):", stdout.split("\t")[0])
            where = "%s line %s" % (m.group(1), m.group(2)) if m else "an ignore rule"
            out.append("%s is ignored (%s), so it would never be committed: remove that rule" % (rel, where))
    return out


def check_overlay(root: str) -> tuple:
    """(exit code, ok lines, problems, notes). 2 when `root` is not an overlay."""
    ok, problems, notes = [], [], []
    if not os.path.isdir(root) or not os.path.isfile(os.path.join(root, MARKER)):
        return 2, ok, ["%s is not a Gnomonkit overlay (no %s)" % (root, MARKER)], notes
    marker, err = read_marker(root)
    name, parts, github = None, [], None
    if err:
        problems.append(err)
    else:
        if marker.get("format") != FORMAT:
            problems.append("%s says format %r; this core reads format %d" % (MARKER, marker.get("format"), FORMAT))
        name = marker.get("name")
        if name_problem(name):
            problems.append("%s name %r: %s" % (MARKER, name, name_problem(name)))
        parts = marker.get("parts") if isinstance(marker.get("parts"), list) else None
        if parts is None:
            problems.append("%s has no parts list" % MARKER)
            parts = []
        for p in parts:
            if p not in PARTS:
                problems.append("%s lists an unknown part %r (known: %s)" % (MARKER, p, ", ".join(PARTS)))
            elif not os.path.exists(_p(root, PARTS[p])):
                problems.append("%s lists %s, but %s is missing" % (MARKER, p, PARTS[p]))
        github = marker.get("github")
        if github is not None and not (isinstance(github, str) and _SLUG.match(github)):
            problems.append("%s github must look like owner/name" % MARKER)
            github = None
        if not problems:
            ok.append("marker: format %d, %r" % (FORMAT, name))

    market, merr = _read_json(_p(root, MARKETPLACE))
    plugin_dirs = []
    before = len(problems)
    if merr:
        problems.append("%s %s" % (MARKETPLACE, merr))
    elif not isinstance(market, dict) or not isinstance(market.get("plugins"), list):
        problems.append("%s must be an object with a plugins list" % MARKETPLACE)
    else:
        if name and market.get("name") != name:
            problems.append("%s is named %r but the marker says %r: they must match (overlay@<name> installs it)"
                            % (MARKETPLACE, market.get("name"), name))
        if not _lists_overlay(market):
            problems.append("%s doesn't list plugins/overlay (add %s)" % (MARKETPLACE, json.dumps(OVERLAY_ENTRY)))
        for entry in market["plugins"]:
            src = entry.get("source") if isinstance(entry, dict) else None
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                problems.append("%s has a plugin entry without a name" % MARKETPLACE)
                continue
            if not isinstance(src, str) or not src.startswith("./"):
                continue   # a plugin from elsewhere (GitHub, a URL): nothing on disk to check
            pdir = os.path.normpath(os.path.join(root, src))
            manifest, perr = _read_json(os.path.join(pdir, ".claude-plugin", "plugin.json"))
            if not os.path.isdir(pdir):
                problems.append("%s lists %s at %s, which doesn't exist" % (MARKETPLACE, entry["name"], src))
            elif perr:
                problems.append("%s/.claude-plugin/plugin.json %s" % (src[2:].rstrip("/"), perr))
            elif not isinstance(manifest, dict) or manifest.get("name") != entry["name"]:
                problems.append("%s/.claude-plugin/plugin.json must be named %r, as the marketplace lists it"
                                % (src[2:].rstrip("/"), entry["name"]))
            else:
                plugin_dirs.append(pdir)
                if not manifest.get("version"):
                    notes.append("%s has no version: installs update only when the version changes" % src[2:])
        if len(problems) == before:
            ok.append("marketplace %r: %d plugin%s" % (market.get("name"), len(market["plugins"]),
                                                       "" if len(market["plugins"]) == 1 else "s"))
    own = _p(root, PARTS["plugin"])
    if os.path.isdir(own) and not any(C.normalise_path(own) == C.normalise_path(d) for d in plugin_dirs):
        plugin_dirs.append(own)   # its skills are checked even while the marketplace doesn't list it

    nskills = 0
    for pdir in plugin_dirs:
        sroot = os.path.join(pdir, "skills")
        for skill in sorted(os.listdir(sroot)) if os.path.isdir(sroot) else ():
            sdir = os.path.join(sroot, skill)
            if not os.path.isdir(sdir):
                continue
            nskills += 1
            md = os.path.join(sdir, "SKILL.md")
            problem = "has no SKILL.md" if not os.path.isfile(md) else skill_problem(md)
            if problem:
                problems.append("%s %s" % (_rel(root, md), problem))
        hooks = os.path.join(pdir, "hooks", "hooks.json")
        if os.path.isfile(hooks):
            data, herr = _read_json(hooks)
            if herr or not isinstance(data, dict) or not isinstance(data.get("hooks"), dict):
                problems.append("%s %s" % (_rel(root, hooks), herr or 'must be an object with a "hooks" object'))
    if plugin_dirs:
        ok.append("%d skill%s" % (nskills, "" if nskills == 1 else "s"))

    prof = _p(root, PARTS["profile"])
    if os.path.isfile(prof):
        found = _profile_check(prof)
        problems += ["%s %s" % (PARTS["profile"], p) for p in found]
        if not found:
            ok.append("profile passes the schema")
    den = _p(root, PARTS["denylist"])
    if os.path.isfile(den):
        try:
            with open(den, encoding="utf-8-sig") as fh:
                nrules, found = parse_denylist(fh.read())
        except (OSError, UnicodeDecodeError) as e:
            nrules, found = 0, ["can't be read (%s)" % e]
        problems += found
        if not found:
            if nrules:
                ok.append("denylist: %d rule%s" % (nrules, "" if nrules == 1 else "s"))
            else:
                notes.append("the denylist has no rules yet, and leakcheck refuses an empty list: add your "
                             "identifiers to %s" % PARTS["denylist"])
    lessons = _p(root, PARTS["lessons"])
    if os.path.isfile(lessons):
        data, lerr = _read_json(lessons)
        if lerr or not isinstance(data, (list, dict)):
            problems.append("%s %s" % (PARTS["lessons"], lerr or "must be a JSON list or object"))

    if _is_repo_root(root):
        problems += ignored_by_mistake(root)
        vok, vproblems, vnotes, slugs = visibility_report(root, [github] if github else [])
        ok += vok
        problems += vproblems
        notes += vnotes
        if not remotes(root) and not slugs:
            notes.append("no remote yet, so it lives only on this machine. Make a PRIVATE one yourself, for example: "
                         "gh repo create <owner>/<name> --private --source %s --remote origin" % _q(root))
    elif shutil.which("git"):
        notes.append("not a git repository yet: run git init here")
        if github:
            _vok, vproblems, vnotes, _ = visibility_report(root, [github])
            ok += _vok
            problems += vproblems
            notes += vnotes
    return (1 if problems else 0), ok, problems, notes


def _overlay_arg(raw: str | None) -> str:
    if raw:
        return C.resolve_path(raw)
    found = configured_overlay()
    if not found:
        raise C.CommandError("no overlay is configured (core's personal_profile doesn't point into one): pass the "
                             "overlay's folder")
    return found


def cmd_check(args) -> int:
    try:
        root = _overlay_arg(args.dir)
    except C.CommandError as e:
        print("gnomonkit: %s" % e)
        return 2
    code, ok, problems, notes = check_overlay(root)
    if code == 2:
        print(problems[0])
        return 2
    print("Overlay at %s" % root)
    for line in ok:
        print("  ok: " + line)
    if problems:
        print("\nProblems:")
        for p in problems:
            print("  - " + p)
    if notes:
        print("\nNotes:")
        for n in notes:
            print("  - " + n)
    print("\n" + ("%d problem%s." % (len(problems), "" if len(problems) == 1 else "s") if problems else "Check passed."))
    return code


# ---- link ----------------------------------------------------------------------------------------
def link_commands(root: str) -> list:
    """The lines `link` prints. Builds text only: nothing is run, not even git."""
    marker, err = read_marker(root)
    if marker is None:
        raise C.CommandError("%s is not a Gnomonkit overlay (%s)" % (root, err), code=2)
    market, _ = _read_json(_p(root, MARKETPLACE))
    name = market.get("name") if isinstance(market, dict) and isinstance(market.get("name"), str) else marker.get("name")
    github = marker.get("github") if isinstance(marker.get("github"), str) else None
    lines = ["Connect the overlay %r at %s. Nothing was run: run these yourself, in a terminal." % (name, root), "",
             "1. Add it as a marketplace and install its plugin:",
             "   claude plugin marketplace add %s" % _q(root),
             "   claude plugin install %s@%s" % (PLUGIN_NAME, name)]
    if github:
        lines.append("   (on another machine, from GitHub: claude plugin marketplace add %s; it needs access to the "
                     "private repository)" % github)
    step = 2
    prof = _p(root, PARTS["profile"])
    if os.path.isfile(prof):
        payload = json.dumps({"personal_profile": prof})
        lines += ["", "%d. Read your personal profile from it (core's personal_profile setting):" % step,
                  "   echo '%s' | claude plugin configure core@gnomonkit --values-stdin" % payload,
                  "   (or start claude, type /plugin configure core@gnomonkit and set Personal profile to %s)" % prof]
        default = C.default_personal_path()
        if os.path.isfile(default) and C.normalise_path(default) != C.normalise_path(prof):
            lines.append("   You already have a profile at %s: to keep its values, copy that file over the "
                         "overlay's first (it is the commented template until you change it)." % default)
        step += 1
    den = _p(root, PARTS["denylist"])
    if os.path.isfile(den):
        lines += ["", "%d. Let leakcheck read your denylist (GNOMONKIT_DENYLIST):" % step]
        if os.name == "nt":
            lines.append("   setx GNOMONKIT_DENYLIST %s      (new terminals see it)" % _q(den))
        else:
            lines.append("   add to your shell profile: export GNOMONKIT_DENYLIST=%s" % _q(den))
        step += 1
    lines += ["", "Then type /reload-plugins in each open session (or start a new one)."]
    return lines


def cmd_link(args) -> int:
    root = _overlay_arg(args.dir)
    for line in link_commands(root):
        print(line)
    return 0


# ---- the parser ----------------------------------------------------------------------------------
def register(sub) -> None:
    p = sub.add_parser("overlay", help="make, convert, check or connect your private overlay",
                       description=__doc__.split("\n\n")[0])
    ps = p.add_subparsers(dest="overlay_cmd", metavar="<init|convert|check|link>")
    q = ps.add_parser("init", help="make a new overlay in an empty or missing folder (runs git init, never pushes)")
    q.add_argument("dir", metavar="DIR")
    q.add_argument("--name", help="the marketplace name (default: from the folder name)")
    q.add_argument("--github", metavar="OWNER/NAME",
                   help="the PRIVATE GitHub repository you will make for it: printed, never created")
    q.set_defaults(func=cmd_init)
    q = ps.add_parser("convert", help="plan bringing an existing repository into the format; --apply copies")
    q.add_argument("repo", metavar="REPO")
    q.add_argument("--name", help="the marketplace name when the repository has none (default: the folder name)")
    q.add_argument("--apply", action="store_true",
                   help="make the copies (never moves, overwrites or deletes); without it nothing changes")
    q.set_defaults(func=cmd_convert)
    q = ps.add_parser("check", help="validate an overlay: exit 0, 1 (problems listed) or 2 (not an overlay)")
    q.add_argument("dir", metavar="DIR", nargs="?", help="default: the overlay your personal profile is in")
    q.set_defaults(func=cmd_check)
    q = ps.add_parser("link", help="print the commands that connect an overlay (runs none of them)")
    q.add_argument("dir", metavar="DIR", nargs="?", help="default: the overlay your personal profile is in")
    q.set_defaults(func=cmd_link)
    p.set_defaults(func=lambda a: (p.print_help(), 0)[1])
