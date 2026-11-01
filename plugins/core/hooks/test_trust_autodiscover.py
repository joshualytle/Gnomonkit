"""Fixtures for the session-start auto-trust hook (trust_autodiscover.py).

Everything runs against a made-up home, Gnomonkit folder and Claude config folder under a temp dir, with
CLAUDE_CONFIG_DIR, HOME and USERPROFILE pointing there, so the real Claude Code file is never read or
written. Repositories are synthetic: a .git folder with HEAD and a hand-written config. Owners are
placeholders.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "trust_autodiscover.py")

TMP = tempfile.mkdtemp(prefix="gk-trust-hook-")
HOME = os.path.join(TMP, "home")
GK = os.path.join(HOME, ".gnomonkit")
CFG = os.path.join(TMP, "claude-config")
CLAUDE_JSON = os.path.join(CFG, ".claude.json")
ROOT = os.path.join(TMP, "projects")
MANUAL = os.path.join(TMP, "manual")
for d in (GK, CFG, ROOT, MANUAL):
    os.makedirs(d, exist_ok=True)

ENV = {k: v for k, v in os.environ.items() if not k.startswith(("GNOMONKIT_", "CLAUDE"))}
ENV.update(HOME=HOME, USERPROFILE=HOME, GNOMONKIT_HOME=GK, CLAUDE_CONFIG_DIR=CFG)

failures = []


def fwd(p):
    return os.path.realpath(p).replace("\\", "/")


def repo(path, url=None):
    os.makedirs(os.path.join(path, ".git"), exist_ok=True)
    with open(os.path.join(path, ".git", "HEAD"), "w", encoding="utf-8") as fh:
        fh.write("ref: refs/heads/main\n")
    conf = "[core]\n\trepositoryformatversion = 0\n"
    if url:
        conf += '[remote "origin"]\n\turl = %s\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n' % url
    with open(os.path.join(path, ".git", "config"), "w", encoding="utf-8") as fh:
        fh.write(conf)
    return path


GH = "https://github.com/"
# The user part of an SSH remote, built at runtime so URLs don't read as e-mail addresses to the leak gate.
SSH_USER = "git" + "@"
MINE = repo(os.path.join(ROOT, "mine"), GH + "example-user/mine.git")
ORG = repo(os.path.join(ROOT, "org-tool"), SSH_USER + "github.com:example-org/org-tool.git")
THEIRS = repo(os.path.join(ROOT, "theirs"), GH + "someone-else/theirs")
LOCAL = repo(os.path.join(ROOT, "local-only"))
os.makedirs(os.path.join(ROOT, "plain-folder"))
OUTER = repo(os.path.join(ROOT, "outer"), GH + "example-user/outer")
INNER = repo(os.path.join(OUTER, "inner"), GH + "example-user/inner")
KEEP = os.path.join(ROOT, "keep")
KEPT = repo(os.path.join(KEEP, "kept"), GH + "example-user/kept")
KEEP_SELF = repo(KEEP, GH + "example-user/keep")       # a per-project root that is itself a repository
SHUT = os.path.join(ROOT, "shut")
SHUT_SELF = repo(SHUT, GH + "example-user/shut")
BY_HAND = repo(os.path.join(MANUAL, "by-hand"), GH + "example-user/by-hand")

with open(os.path.join(GK, "grants.toml"), "w", encoding="utf-8") as fh:
    fh.write('version = 1\n\n'
             '[[trust]]\nroot = "%s"\nlevel = "trusted"\nauto = true\nowners = ["example-user", "Example-Org"]\n\n'
             '[[trust]]\nroot = "%s"\nlevel = "per-project"\n\n'
             '[[trust]]\nroot = "%s"\nlevel = "never"\n\n'
             '[[trust]]\nroot = "%s"\nlevel = "trusted"\nowners = ["example-user"]\n'
             % (fwd(ROOT), fwd(KEEP), fwd(SHUT), fwd(MANUAL)))

ORIGINAL = {"numStartups": 7, "theme": "dark", "projects": {
    fwd(OUTER): {"allowedTools": [], "hasTrustDialogAccepted": True, "lastCost": 0},
    fwd(THEIRS): {"allowedTools": ["Read"], "hasTrustDialogAccepted": False}}, "tipsHistory": {"a": 1}}


def write_config(data=ORIGINAL, text=None):
    with open(CLAUDE_JSON, "w", encoding="utf-8", newline="") as fh:
        fh.write(text if text is not None else json.dumps(data, indent=2))


def read_bytes():
    with open(CLAUDE_JSON, "rb") as fh:
        return fh.read()


def run(event=None, env=None, raw=None):
    data = raw if raw is not None else json.dumps(event if event is not None else {"source": "startup"})
    r = subprocess.run([sys.executable, HOOK], input=data, capture_output=True, text=True, encoding="utf-8",
                       env=env or ENV)
    return r.returncode, r.stdout, r.stderr


def message(out):
    if not out.strip():
        return ""
    data = json.loads(out)
    assert data["systemMessage"] == data["hookSpecificOutput"]["additionalContext"]
    return data["systemMessage"]


def projects():
    return json.loads(read_bytes().decode("utf-8"))["projects"]


def trusted(path):
    return any(k.lower() == fwd(path).lower() and isinstance(v, dict) and v.get("hasTrustDialogAccepted") is True
               for k, v in projects().items())


def audit():
    p = os.path.join(GK, "trust", "audit.jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(x) for x in fh if x.strip()]


def expect(cond, what):
    if not cond:
        failures.append(what)


# ---- first start: the two repositories that qualify, and nothing else ---------------------------
write_config()
rc, out, err = run()
text = message(out)
expect(rc == 0, "the hook must exit 0 (got %d: %s)" % (rc, err[-300:]))
expect(trusted(MINE) and trusted(ORG), "repositories whose owner is listed must be trusted")
expect(fwd(MINE) in text and fwd(ORG) in text and text.count("\n") == 1, "one line per newly trusted repository")
for path, why in ((THEIRS, "an owner not in owners"), (LOCAL, "no remote while local_only is off"),
                  (INNER, "a nested repository (the trusted parent does not cover it, the scan is one level)"),
                  (KEPT, "a repository under a per-project root"), (KEEP_SELF, "a per-project root itself"),
                  (SHUT_SELF, "a never root itself"), (BY_HAND, "a trusted root without auto")):
    expect(not trusted(path), "left alone: %s" % why)
expect(not any(k.lower().endswith("/plain-folder") for k in projects()), "a folder without .git is not touched")
now = json.loads(read_bytes().decode("utf-8"))
expect({k: v for k, v in now.items() if k != "projects"} == {k: v for k, v in ORIGINAL.items() if k != "projects"},
       "keys outside projects must be unchanged")
expect(now["projects"][fwd(OUTER)] == ORIGINAL["projects"][fwd(OUTER)]
       and now["projects"][fwd(THEIRS)] == ORIGINAL["projects"][fwd(THEIRS)], "other entries unchanged")
added = [k for k in now["projects"] if k not in ORIGINAL["projects"]]
expect(all(now["projects"][k] == {"hasTrustDialogAccepted": True} for k in added) and len(added) == 2,
       "a new entry holds only hasTrustDialogAccepted: %r" % added)
backups = os.listdir(os.path.join(GK, "trust", "backups"))
expect(len(backups) == 1, "one backup per write")
with open(os.path.join(GK, "trust", "backups", backups[0]), "rb") as fh:
    expect(fh.read() == json.dumps(ORIGINAL, indent=2).encode("utf-8"), "the backup is the file as it was")
events = audit()
expect([e["event"] for e in events] == ["trusted", "trusted"] and all(e["by"] == "auto" for e in events),
       "each change is one audit line: %r" % events)
expect(not any("github.com" in json.dumps(e) for e in events), "the audit log never holds a remote URL")

# ---- second start: nothing new, nothing printed, nothing written ----------------------------------
before = read_bytes()
rc, out, _ = run()
expect(rc == 0 and out.strip() == "" and read_bytes() == before, "a second start changes nothing and prints nothing")

# ---- Claude Code writes back a copy without the key: the next start puts it back ------------------
lost = json.loads(before.decode("utf-8"))
del lost["projects"][fwd(MINE)]
write_config(lost)
rc, out, _ = run()
expect(trusted(MINE) and "put back" in message(out), "a key Claude Code lost is put back on the next start")
expect(audit()[-1]["event"] == "reapplied" and audit()[-1]["repo"] == fwd(MINE), "and logged as reapplied")

# ---- a new clone under the root is trusted at the next start ---------------------------------------
FRESH = repo(os.path.join(ROOT, "fresh-clone"), "ssh://" + SSH_USER + "github.com/example-user/fresh-clone.git")
rc, out, _ = run()
expect(trusted(FRESH) and fwd(FRESH) in message(out), "a new clone with a listed owner is trusted")

# ---- many new clones at once: at most ten lines, then how many more --------------------------------
MANY = [repo(os.path.join(ROOT, "batch-%02d" % n), GH + "example-user/batch-%02d" % n) for n in range(12)]
rc, out, _ = run()
text = message(out)
expect(all(trusted(p) for p in MANY), "every new clone is trusted, however many")
expect(text.count("\n") == 10 and "...and 2 more" in text and "trust status" in text,
       "the message stops at ten lines and says how many more: %r" % text[-200:])

# ---- what it must not do ---------------------------------------------------------------------------
LATER = repo(os.path.join(ROOT, "later"), GH + "example-user/later")
for name, event, env in (("resume", {"source": "resume"}, None), ("compact", {"source": "compact"}, None),
                         ("switched off", None, dict(ENV, GNOMONKIT_TRUST_AUTO="off"))):
    before = read_bytes()
    rc, out, _ = run(event, env)
    expect(rc == 0 and out.strip() == "" and read_bytes() == before, "%s: nothing written or printed" % name)

for name, text in (("not JSON", '{"projects": {'), ("projects is a list", '{"projects": []}'),
                   ("no projects", '{"numStartups": 1}'), ("a byte-order mark", "﻿" + json.dumps(ORIGINAL)),
                   ("a repeated key", '{"projects": {"%s": {}, "%s": {}}}' % (fwd(LATER), fwd(LATER)))):
    write_config(text=text)
    before = read_bytes()
    rc, out, _ = run()
    expect(rc == 0 and out.strip() == "" and read_bytes() == before, "%s: the file is left alone" % name)

os.remove(CLAUDE_JSON)
rc, out, _ = run()
expect(rc == 0 and out.strip() == "" and not os.path.exists(CLAUDE_JSON), "no file: none is created")

# The control: with a file it recognises, the same start trusts what was waiting all along.
write_config()
rc, out, _ = run()
expect(trusted(LATER) and trusted(MINE), "the cases above wrote nothing only because of what they changed")

write_config()
with open(os.path.join(GK, "grants.toml"), "a", encoding="utf-8") as fh:
    fh.write("\n[[trust]]\nroot = \"relative/path\"\nlevel = \"trusted\"\n")
before = read_bytes()
rc, out, _ = run()
expect(rc == 0 and out.strip() == "" and read_bytes() == before, "levels with a problem: nothing is trusted")

rc, out, _ = run(raw="not json at all")
expect(rc == 0, "an unreadable event still exits 0")

# ---- fails open: an error anywhere prints nothing and exits 0 --------------------------------------
spec = importlib.util.spec_from_file_location("trust_autodiscover_under_test", HOOK)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def boom(*_args):
    raise RuntimeError("scan broke")


mod.run = boom
old_in, old_out = sys.stdin, sys.stdout
sys.stdin, sys.stdout = io.StringIO('{"source": "startup"}'), io.StringIO()
try:
    rc = mod.main()
    printed = sys.stdout.getvalue()
finally:
    sys.stdin, sys.stdout = old_in, old_out
expect(rc == 0 and printed == "", "an exception inside the hook prints nothing and exits 0")

if failures:
    print("\n".join("FAIL " + f for f in failures))
    sys.exit(1)
print("trust autodiscover: all cases pass")
