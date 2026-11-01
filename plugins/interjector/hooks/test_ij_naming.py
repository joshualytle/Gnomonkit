#!/usr/bin/env python3
"""Standalone tests for ij_naming.py (session titles by the naming scheme, `<repo>:<task>` by default); exits
non-zero on failure.

Synthetic data only: temp git repos with made-up names, made-up prompts and titles.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "ij_naming.py")
sys.path.insert(0, HERE)
import ij_naming as N  # noqa: E402

FAILS = []


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test" + "@" + "example.invalid", "-c",
                    "init.defaultBranch=main", *args], cwd=cwd, check=True, stdin=subprocess.DEVNULL,
                   capture_output=True)


def run(payload, raw=None, **extra):
    # The user's own profile and naming settings never reach the hook: only what a test passes.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GNOMONKIT_NAMING_") and k != "CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE"}
    env.update({"GNOMONKIT_HOME": HOME, "GNOMONKIT_PERSONAL_PROFILE": os.path.join(BASE, "no-profile.toml"),
                **extra})
    data = raw if raw is not None else json.dumps(payload)
    t0 = time.perf_counter()
    r = subprocess.run([sys.executable, HOOK], input=data.encode("utf-8"), capture_output=True, env=env,
                       timeout=60)
    took = time.perf_counter() - t0
    out = r.stdout.decode("utf-8").strip()
    return r.returncode, (json.loads(out) if out else None), took


def title_of(out):
    return ((out or {}).get("hookSpecificOutput") or {}).get("sessionTitle")


def start(cwd, source="startup", title=None):
    p = {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": cwd, "source": source}
    if title is not None:
        p["session_title"] = title
    return p


def prompt(cwd, text, title=None):
    p = {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": cwd, "prompt": text}
    if title is not None:
        p["session_title"] = title
    return p


BASE = tempfile.mkdtemp(prefix="ij-name-")
HOME = os.path.join(BASE, "home")
os.makedirs(HOME)
REPO = os.path.join(BASE, "demo-repo")
os.makedirs(os.path.join(REPO, "src"))
git(REPO, "init", "-q")
with open(os.path.join(REPO, "README"), "w", encoding="utf-8") as fh:
    fh.write("synthetic\n")
git(REPO, "add", "README")
git(REPO, "commit", "-q", "-m", "init")
WT = os.path.join(BASE, "demo-wt")
git(REPO, "worktree", "add", "-q", "-b", "feat-x", WT)
PLAIN = os.path.join(BASE, "plain-folder")
os.makedirs(PLAIN)

# --- SessionStart --------------------------------------------------------------------------------
rc, out, took = run(start(REPO))
check("startup, no title: placeholder named after the repo", rc == 0 and title_of(out) == "demo-repo:new-session")
check("output is SessionStart hookSpecificOutput", (out or {}).get("hookSpecificOutput", {}).get("hookEventName")
      == "SessionStart")
rc, out, _ = run(start(os.path.join(REPO, "src")))
check("a subfolder still names the repo", title_of(out) == "demo-repo:new-session")
rc, out, _ = run(start(WT))
check("a linked worktree is main repo plus branch", title_of(out) == "demo-repo@feat-x:new-session")
rc, out, _ = run(start(PLAIN))
check("outside git: the folder name", title_of(out) == "plain-folder:new-session")
rc, out, _ = run(start(REPO, title="my chosen title"))
check("startup with a user-set title: untouched", rc == 0 and out is None)
rc, out, _ = run(start(REPO, title="demo-repo:new-session"))
check("startup with our own placeholder already set: nothing to do", out is None)
rc, out, _ = run(start(REPO, source="clear"))
check("clear: no title (sessionTitle does not apply)", out is None)
rc, out, _ = run(start(REPO, source="resume"))
check("resume of an untitled session: placeholder", title_of(out) == "demo-repo:new-session")

# --- UserPromptSubmit ----------------------------------------------------------------------------
TASK = "Please fix the login redirect bug in the auth module, it loops forever"
rc, out, took_fast = run(prompt(REPO, TASK, title="demo-repo:new-session"))
t = title_of(out)
check("first prompt replaces the placeholder with <repo>:<task>",
      rc == 0 and t == "demo-repo:fix-login-redirect-bug-auth-module")
check("prompt output is UserPromptSubmit hookSpecificOutput",
      (out or {}).get("hookSpecificOutput", {}).get("hookEventName") == "UserPromptSubmit")
rc, out, _ = run(prompt(WT, TASK, title="demo-repo@feat-x:new-session"))
check("placeholder's repo part is kept (worktree)", (title_of(out) or "").startswith("demo-repo@feat-x:fix-login"))
rc, out, _ = run(prompt(REPO, TASK))
check("no title at all: named from cwd and the prompt", title_of(out) == "demo-repo:fix-login-redirect-bug-auth-module")
for user_title in ("my chosen title", "demo-repo:auth-work", "Release prep", "x:new-session-2"):
    rc, out, _ = run(prompt(REPO, TASK, title=user_title))
    check(f"user-set title {user_title!r} is never overwritten", rc == 0 and out is None)
rc, out, _ = run(prompt(REPO, "/model", title="demo-repo:new-session"))
check("a slash command is not a task: placeholder stays", out is None)
rc, out, _ = run(prompt(REPO, "<task-notification>done</task-notification>", title="demo-repo:new-session"))
check("an automatic notice is not a task", out is None)
rc, out, _ = run(prompt(REPO, "ok thanks", title="demo-repo:new-session"))
check("a prompt of filler words leaves the placeholder", out is None)
rc, out, _ = run(prompt(REPO, TASK, title="demo-repo:new-session"), GNOMONKIT_SESSION_NAMES="0")
check("GNOMONKIT_SESSION_NAMES=0 turns naming off", out is None)

# --- a naming scheme of the user's own ------------------------------------------------------------
GK = {"GNOMONKIT_NAMING_PREFIX": "gk", "GNOMONKIT_NAMING_PRIMARY": "{prefix} {repo} - {task}"}
rc, out, _ = run(start(REPO), **GK)
check("custom primary template: the placeholder follows it", title_of(out) == "gk demo-repo - new-session")
rc, out, _ = run(prompt(REPO, TASK, title="gk demo-repo - new-session"), **GK)
check("custom primary template: the first prompt renames with it",
      title_of(out) == "gk demo-repo - fix-login-redirect-bug-auth-module")
rc, out, _ = run(prompt(WT, TASK, title="gk demo-repo@feat-x - new-session"), **GK)
check("custom primary template: the placeholder's repo part is kept",
      (title_of(out) or "").startswith("gk demo-repo@feat-x - fix-login"))
rc, out, _ = run(prompt(REPO, TASK, title="demo-repo:new-session"), **GK)
check("a placeholder made before the scheme changed is still replaced",
      title_of(out) == "gk demo-repo - fix-login-redirect-bug-auth-module")
rc, out, _ = run(prompt(REPO, TASK, title="gk demo-repo - auth-work"), **GK)
check("custom scheme: a title with a real task is left alone", out is None)
rc, out, _ = run(start(REPO), GNOMONKIT_NAMING_PRIMARY="{repo}{task}")
check("an invalid scheme: the defaults, silently", rc == 0 and title_of(out) == "demo-repo:new-session")
PROFILE = os.path.join(BASE, "personal.toml")
with open(PROFILE, "w", encoding="utf-8") as fh:
    fh.write('[naming]\nprimary = "{date} {repo}:{task}"\n')
days = {time.strftime("%Y-%m-%d")}
rc, out, _ = run(start(REPO), GNOMONKIT_PERSONAL_PROFILE=PROFILE)
days.add(time.strftime("%Y-%m-%d"))                      # a run across midnight may make either date
check("[naming] in the personal profile sets the scheme",
      title_of(out) in {d + " demo-repo:new-session" for d in days})
rc, out, _ = run(start(REPO), GNOMONKIT_PERSONAL_PROFILE=PROFILE, GNOMONKIT_NAMING_PRIMARY="{repo} = {task}")
check("the environment wins over the profile", title_of(out) == "demo-repo = new-session")

# --- the slug ------------------------------------------------------------------------------------
long = "refactor " + " ".join(f"component{i}abc" for i in range(20))
check("slug is at most 40 characters", 0 < len(N.slug(long)) <= 40)
check("slug is deterministic", N.slug(TASK) == N.slug(TASK))
secret = "gh" + "p_" + "Z" * 30
key_line = "deploy with api" + "_key=" + "q" * 24 + " and token " + secret
s = N.slug(key_line)
check("secrets never reach the slug", "zzzz" not in s and "qqqq" not in s and "ghp" not in s and "deploy" in s)
pasted = 'summarise this <pasted_content id="1">\nsecretword alpha beta\n</pasted_content id="1"> quickly'
check("pasted content is left out", "secretword" not in N.slug(pasted) and N.slug(pasted).startswith("summarise"))
check("urls and code blocks are left out",
      N.slug("check https://example.invalid/a/b then ```rm -rf x``` tidy docs") == "check-tidy-docs")
check("hash-like ids are dropped", "4f9a2c81d3" not in N.slug("revert 4f9a2c81d3 commit"))

# --- robustness and cost -------------------------------------------------------------------------
rc, out, _ = run(None, raw="not json at all")
check("garbage input: exit 0, no output", rc == 0 and out is None)
rc, out, _ = run({"hook_event_name": "SessionStart", "cwd": os.path.join(BASE, "missing"), "source": "startup"})
check("missing cwd: still exit 0", rc == 0)
check("nothing is written to disk", os.listdir(HOME) == [])
print(f"  info  startup with git {took * 1000:.0f} ms, prompt with placeholder {took_fast * 1000:.0f} ms")
check("hook finishes well inside its timeout", took < 3 and took_fast < 3)

if FAILS:
    print(f"{len(FAILS)} failed")
    sys.exit(1)
print("all passed")
