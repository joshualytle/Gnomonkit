"""Fixtures for the grants guard (grants_guard.py).

Both directions matter: a missed edit of the grants file lets an agent grant itself, and a false block on
ordinary work trains people to switch the guard off. Synthetic data only: a made-up home, Gnomonkit folder
and Claude config folder under a temp dir; payloads are built as Python objects, never through a shell.
"""
import base64
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "grants_guard.py")
BLOCK, ALLOW = 2, 0

TMP = tempfile.mkdtemp(prefix="gk-guard-")
HOME = os.path.join(TMP, "home")
GK = os.path.join(HOME, ".gnomonkit")
ALT = os.path.join(TMP, "elsewhere", "gk")              # a GNOMONKIT_HOME outside the home folder
CFG = os.path.join(TMP, "claude-config")
PROJECT = os.path.join(TMP, "projects", "demo-app")
for d in (GK, ALT, CFG, PROJECT):
    os.makedirs(d, exist_ok=True)
GRANTS = os.path.join(GK, "grants.toml")
with open(GRANTS, "w", encoding="utf-8") as fh:
    fh.write("version = 1\n")
with open(os.path.join(HOME, ".claude.json"), "w", encoding="utf-8") as fh:
    fh.write("{}\n")

ENV = {k: v for k, v in os.environ.items() if not k.startswith("GNOMONKIT_")
       and k not in ("CLAUDE_CONFIG_DIR", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID")}
ENV.update(HOME=HOME, USERPROFILE=HOME)

# Sessions: a made-up `claude agents --json --all` listing, and grants that let demo-app reach allowed-app only.
ALLOWED = os.path.join(TMP, "projects", "allowed-app")
OTHER = os.path.join(TMP, "projects", "other-app")
for d in (ALLOWED, OTHER):
    os.makedirs(d, exist_ok=True)
REQ_ID = "1111aaaa-0000-4000-8000-000000000001"
LISTING = [
    {"id": "r1", "sessionId": REQ_ID, "name": "demo-app:main", "cwd": PROJECT, "pid": 1},
    {"id": "ok1", "sessionId": "2222bbbb-0000-4000-8000-000000000002", "name": "allowed-app:work", "cwd": ALLOWED},
    {"id": "no1", "sessionId": "3333cccc-0000-4000-8000-000000000003", "name": "other-app:work", "cwd": OTHER},
]
FAKE = os.path.join(TMP, "fake_claude.py")
with open(os.path.join(TMP, "listing.json"), "w", encoding="utf-8") as fh:
    json.dump(LISTING, fh)
with open(FAKE, "w", encoding="utf-8") as fh:
    fh.write("import os, sys\n"
             "if os.environ.get('FAKE_CLAUDE_FAIL'): sys.exit(1)\n"
             "sys.stdout.write(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'listing.json')).read())\n")
ENV["GNOMONKIT_CLAUDE_CMD"] = json.dumps([sys.executable, FAKE])
ENV_NO_LIST = dict(ENV, FAKE_CLAUDE_FAIL="1")


def _slash(p):
    return p.replace("\\", "/")


with open(GRANTS, "w", encoding="utf-8") as fh:
    fh.write('version = 1\n\n[[grant]]\nfrom = "%s"\nto = ["%s"]\nactions = ["spawn", "stop", "message"]\n'
             'agents = ["claude"]\n' % (_slash(PROJECT), _slash(ALLOWED)))

failures = []


def run(payload, env=None, raw=None):
    data = raw if raw is not None else json.dumps(payload)
    r = subprocess.run([sys.executable, HOOK], input=data, capture_output=True, text=True, encoding="utf-8",
                       env=env or ENV)
    return r.returncode, r.stdout, r.stderr


def edit(tool, path, cwd=PROJECT, key="file_path"):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": cwd, "tool_input": {key: path}}


def shell(cmd, tool="Bash"):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": PROJECT, "session_id": REQ_ID,
            "tool_input": {"command": cmd}}


def send(to):
    return {"hook_event_name": "PreToolUse", "tool_name": "SendMessage", "cwd": PROJECT, "session_id": REQ_ID,
            "tool_input": {"to": to, "message": "hello"}}


def check(name, payload, want, env=None, raw=None):
    rc, out, err = run(payload, env, raw)
    if rc != want:
        failures.append("%s: exit %d, wanted %d (%s)" % (name, rc, want, err.strip()[:200]))
    elif want == BLOCK and "grants guard" not in err:
        failures.append("%s: blocked without saying why" % name)
    if out.strip():
        failures.append("%s: printed to stdout (the guard never approves or rewrites a call): %r" % (name, out[:80]))


# --- file tools: the grants file, however it is reached --------------------------------------------------
check("Write to the grants file", edit("Write", GRANTS), BLOCK)
check("Edit to the grants file", edit("Edit", GRANTS), BLOCK)
check("MultiEdit to the grants file", edit("MultiEdit", GRANTS), BLOCK)
check("NotebookEdit naming it", edit("NotebookEdit", GRANTS, key="notebook_path"), BLOCK)
check("relative path from the Gnomonkit folder", edit("Write", "grants.toml", cwd=GK), BLOCK)
check("relative path with ..", edit("Edit", os.path.join("..", "..", "home", ".gnomonkit", "x", "..", "grants.toml"),
                                    cwd=PROJECT), BLOCK)
check("upper case (case-insensitive file systems)", edit("Write", os.path.join(GK, "GRANTS.TOML")), BLOCK)
check("~ in the path", edit("Write", "~/.gnomonkit/grants.toml"), BLOCK)
check("the backup beside it", edit("Write", GRANTS + ".bak"), BLOCK)
check("an atomic-write temp file beside it", edit("Write", os.path.join(GK, ".grants.toml.abc123.tmp")), BLOCK)
check("any file named grants.toml (a GNOMONKIT_HOME of the agent's own)",
      edit("Write", os.path.join(TMP, "agent-made", "grants.toml")), BLOCK)
env_alt = dict(ENV, GNOMONKIT_HOME=ALT)
check("the grants file under GNOMONKIT_HOME", edit("Write", os.path.join(ALT, "grants.toml")), BLOCK, env=env_alt)

link_dir = os.path.join(TMP, "links")
os.makedirs(link_dir, exist_ok=True)
try:
    os.symlink(GRANTS, os.path.join(link_dir, "innocent.txt"))
    check("a symlink to the grants file", edit("Edit", os.path.join(link_dir, "innocent.txt")), BLOCK)
    os.symlink(GK, os.path.join(link_dir, "gkdir"), target_is_directory=True)
    check("through a symlinked folder", edit("Write", os.path.join(link_dir, "gkdir", "grants.toml")), BLOCK)
except (OSError, NotImplementedError):
    print("skipped: symlinks (this machine can't make them without extra rights)")
try:
    os.link(GRANTS, os.path.join(link_dir, "hard.txt"))
    check("a hard link to the grants file", edit("Write", os.path.join(link_dir, "hard.txt")), BLOCK)
except (OSError, NotImplementedError):
    print("skipped: hard links")

# --- file tools: Claude Code's trust record --------------------------------------------------------------
check("Write to ~/.claude.json", edit("Write", os.path.join(HOME, ".claude.json")), BLOCK)
check("Edit to .claude.json under CLAUDE_CONFIG_DIR", edit("Edit", os.path.join(CFG, ".claude.json")), BLOCK,
      env=dict(ENV, CLAUDE_CONFIG_DIR=CFG))

# --- Gnomonkit's folder-trust records (~/.gnomonkit/trust/): what it puts back at session start -----------
TRUST_DIR = os.path.join(GK, "trust")
check("Write to the folder-trust state", edit("Write", os.path.join(TRUST_DIR, "state.json")), BLOCK)
check("Edit to a backup of Claude Code's file",
      edit("Edit", os.path.join(TRUST_DIR, "backups", "claude.json.20260101T000000Z-1-0.bak")), BLOCK)
check("Write to the folder-trust audit log", edit("Write", os.path.join(TRUST_DIR, "audit.jsonl")), BLOCK)
check("the folder itself", edit("Write", TRUST_DIR), BLOCK)
check("upper case", edit("Write", os.path.join(GK, "TRUST", "State.json")), BLOCK)
check("the folder-trust records under GNOMONKIT_HOME", edit("Write", os.path.join(ALT, "trust", "state.json")),
      BLOCK, env=env_alt)
check("a file beside it whose name starts the same", edit("Write", os.path.join(GK, "trusted-notes.md")), ALLOW)
check("rm the state", shell("rm ~/.gnomonkit/trust/state.json"), BLOCK)
check("PowerShell Remove-Item on the folder", shell(r"Remove-Item -Recurse $env:USERPROFILE\.gnomonkit\trust",
                                                    tool="PowerShell"), BLOCK)
check("a one-liner naming trust/state.json", shell("python -c \"import os; os.remove('trust/state.json')\""), BLOCK)
check("the folder of a GNOMONKIT_HOME elsewhere, spelled out", shell('rm -r "%s"' % os.path.join(ALT, "trust")),
      BLOCK, env=env_alt)
check("a folder that only starts like it", shell("ls ~/.gnomonkit/trusted-notes"), ALLOW)
check("a glob for the folder", shell("rm -rf ~/.gnomonkit/t*"), BLOCK)
check("a glob for the state file in it", shell("cat ~/.gnomonkit/tr?st/state.json"), BLOCK)
check("quoted, in pieces", shell("rm \"$HOME/.gnomonkit/\"'trust'/state.json"), BLOCK)
check("a folder elsewhere that only starts like it, spelled out",
      shell('ls "%s"' % os.path.join(ALT, "trusted-notes")), ALLOW, env=env_alt)
check("a commit message that mentions the folder",
      shell('git commit -m "docs: say what ~/.gnomonkit/trust/ holds"'), ALLOW)

# --- file tools: ordinary work goes through --------------------------------------------------------------
check("Write to a project file", edit("Write", os.path.join(PROJECT, "src", "app.py")), ALLOW)
check("Edit to the personal profile", edit("Edit", os.path.join(GK, "personal.toml")), ALLOW)
check("a file whose name only contains the word", edit("Write", os.path.join(PROJECT, "grants_notes.md")), ALLOW)
check("a project's own .claude folder", edit("Write", os.path.join(PROJECT, ".claude", "settings.json")), ALLOW)
check("a Read is not this hook's business", {"tool_name": "Read", "tool_input": {"file_path": GRANTS}}, ALLOW)

# --- shell commands --------------------------------------------------------------------------------------
check("cat the grants file", shell("cat ~/.gnomonkit/grants.toml"), BLOCK)
check("append through $HOME", shell('echo "x" >> "$HOME/.gnomonkit/GRANTS.toml"'), BLOCK)
check("PowerShell Set-Content", shell(r"Set-Content $env:USERPROFILE\.gnomonkit\grants.toml 'version = 1'",
                                      tool="PowerShell"), BLOCK)
check("a python one-liner naming it", shell("python -c \"open('grants.toml','a').write('x')\""), BLOCK)
check("jq over .claude.json", shell("jq '.projects' ~/.claude.json"), BLOCK)
check("gnomonkit grants add", shell('python "/opt/core/gnomonkit.py" grants add --from /a --to /b --actions spawn'),
      BLOCK)
check("gnomonkit grants remove via the launcher", shell("gnomonkit grants remove 1"), BLOCK)
check("gnomonkit.cmd grants add", shell("gnomonkit.cmd grants add --from C:/a --to C:/b --actions stop",
                                        tool="PowerShell"), BLOCK)
check("gnomonkit setup security", shell("gnomonkit setup security"), BLOCK)
check("gnomonkit trust sync", shell("gnomonkit trust sync"), BLOCK)
check("inside script -c", shell("script -qc 'gnomonkit grants add --from /a --to /b --actions spawn' /dev/null"),
      BLOCK)
check("gnomonkit grants list", shell("gnomonkit grants list"), ALLOW)
check("gnomonkit grants check", shell('python "/opt/core/gnomonkit.py" grants check --from /a --to /b --action spawn'),
      ALLOW)
check("gnomonkit grants deny-rules", shell("gnomonkit grants deny-rules"), ALLOW)
check("gnomonkit trust status", shell("gnomonkit trust status"), ALLOW)
check("gnomonkit setup status", shell("gnomonkit setup status"), ALLOW)
check("gnomonkit profile set", shell("gnomonkit profile set communication.reply_length=short"), ALLOW)
check("an ordinary command", shell("git status && python -m pytest -q"), ALLOW)
check("dispatch itself", shell('python dispatch.py spawn demo-app --task "fix the build" --dry-run'), ALLOW)
check("an empty command", shell(""), ALLOW)

# --- shell commands: the name however it is spelled ------------------------------------------------------
check("Windows short name of .claude.json", shell("cat ~/CLAUDE~1.JSO"), BLOCK)
check("Windows short name of grants.toml", shell("cat ~/.gnomonkit/GRANTS~1.TOM"), BLOCK)
check("a glob for .claude.json", shell("cat ~/.claude.js[o]n"), BLOCK)
check("a glob for grants.toml", shell("cat ~/.gnomonkit/g*.toml"), BLOCK)
check("every file in the Gnomonkit folder", shell("cat ~/.gnomonkit/*"), BLOCK)
check("quotes inside the name", shell("cat ~/.gnomonkit/gra''nts.toml"), BLOCK)
check("a quoted subcommand word", shell('gnomonkit.py grants "add" --from /a --to /b --actions spawn'), BLOCK)
check("a quoted area word", shell("gnomonkit 'trust' sync"), BLOCK)
check("inside $(...) of a commit message", shell('git commit -m "$(cat ~/.gnomonkit/grants.toml)"'), BLOCK)
check("grep reading .claude.json", shell("grep projects ~/.claude.json"), BLOCK)
check("git -m that isn't a message", shell("git checkout -m -- ~/.gnomonkit/grants.toml"), BLOCK)
check("bash -c", shell("bash -c 'cat ~/.claude.json'"), BLOCK)
encoded = base64.b64encode("Get-Content ~/.claude.json".encode("utf-16-le")).decode()
check("powershell -EncodedCommand", shell("powershell -NoProfile -EncodedCommand " + encoded, tool="PowerShell"),
      BLOCK)
check("a here-document fed to python", shell("python - <<'EOF'\nopen('grants.toml', 'a').write('x')\nEOF"), BLOCK)
check("a here-document fed to bash", shell("bash <<'EOF'\ngnomonkit grants add --from /a --to /b\nEOF"), BLOCK)
check("a glob in the project", shell("ls * && cat src/*.py *.toml"), ALLOW)
check("a commit message about the guard", shell('git commit -m "guard: block grants.toml and .claude.json edits"'),
      ALLOW)
check("a commit message from a here-document",
      shell("git commit -m \"$(cat <<'EOF'\nguard grants.toml edits\nEOF\n)\""), ALLOW)
check("a PR title and body", shell('gh pr create --title "grants.toml guard" --body "covers .claude.json too"'),
      ALLOW)
check("a search for the name", shell("grep -rn grants.toml docs/ && rg -e '.claude.json' src"), ALLOW)

# --- the usage allocation ------------------------------------------------------------------------------
check("Edit the usage allocation", edit("Edit", os.path.join(GK, "usage-allocation.toml")), BLOCK)
check("Write its backup", edit("Write", os.path.join(GK, "usage-allocation.toml.bak")), BLOCK)
check("Write the usage cache", edit("Write", os.path.join(GK, "usage-allocation.cache.json")), BLOCK)
check("rm the usage cache", shell("rm ~/.gnomonkit/usage-allocation.cache.json"), BLOCK)
check("a python one-liner raising a share",
      shell("python -c \"open('usage-allocation.toml','a').write('share = 100')\""), BLOCK)
check("Windows short name of the allocation", shell("cat ~/.gnomonkit/USAGE-~1.TOM"), BLOCK)
check("gnomonkit usage allow", shell("gnomonkit usage allow /work/alpha 100"), BLOCK)
check("gnomonkit usage lift", shell('python "/opt/core/gnomonkit.py" usage lift /work/alpha'), BLOCK)
check("gnomonkit usage budget", shell("gnomonkit usage budget 900 usd"), BLOCK)
check("gnomonkit usage show", shell("gnomonkit usage show"), ALLOW)
check("gnomonkit usage", shell("gnomonkit usage --json"), ALLOW)
check("a file whose name only starts the same", edit("Write", os.path.join(PROJECT, "usage-allocation-notes.md")),
      ALLOW)

# --- shell commands: the variables the grants check reads ------------------------------------------------
check("clearing CLAUDECODE for one command", shell("CLAUDECODE= python dispatch.py spawn demo-app"), BLOCK)
check("env -u CLAUDECODE", shell("env -u CLAUDECODE python dispatch.py list"), BLOCK)
check("env -i", shell("env -i bash -c 'python dispatch.py list'"), BLOCK)
check("unset the session id", shell("unset CLAUDE_CODE_SESSION_ID && python dispatch.py list"), BLOCK)
check("export GNOMONKIT_HOME", shell("export GNOMONKIT_HOME=/tmp/mine"), BLOCK)
check("PowerShell $env: assignment", shell("$env:CLAUDECODE = ''", tool="PowerShell"), BLOCK)
check("PowerShell Remove-Item Env:", shell("Remove-Item Env:CLAUDE_PID", tool="PowerShell"), BLOCK)
check("SetEnvironmentVariable", shell("[Environment]::SetEnvironmentVariable('GNOMONKIT_HOME', 'C:/mine')",
                                      tool="PowerShell"), BLOCK)
check("cmd set", shell('cmd /c "set CLAUDECODE= && python dispatch.py list"'), BLOCK)
check("reading the variable", shell('echo "$CLAUDECODE"'), ALLOW)
check("reading it in PowerShell", shell("Write-Output $env:CLAUDECODE", tool="PowerShell"), ALLOW)
check("another Gnomonkit variable", shell("export GNOMONKIT_DENYLIST=/tmp/deny.txt"), ALLOW)

# --- shell commands: sessions, against the grants ---------------------------------------------------------
check("claude stop, granted", shell("claude stop allowed-app:work"), ALLOW)
check("claude stop, not granted", shell("claude stop other-app:work"), BLOCK)
check("claude stop by id, not granted", shell("claude stop 3333cccc-0000-4000-8000-000000000003"), BLOCK)
check("claude stop, no such session", shell("claude stop nobody"), BLOCK)
check("claude stop, no session named", shell("claude stop"), BLOCK)
check("claude stop, the list can't be read", shell("claude stop allowed-app:work"), BLOCK, env=ENV_NO_LIST)
check("claude kill behind timeout", shell("timeout 5 claude kill other-app:work"), BLOCK)
check("claude rm", shell("claude rm other-app:work"), BLOCK)
check("claude rm --help with 2>&1 acts on nothing", shell("claude rm --help 2>&1"), ALLOW)
check("claude stop --help into a pipe", shell("claude stop --help 2>&1 | head"), ALLOW)
check("claude rm -h to /dev/null", shell("claude rm -h >/dev/null"), ALLOW)
check("a redirection is not a session name", shell("claude stop allowed-app:work 2>&1"), ALLOW)
check("a redirection doesn't hide the target", shell("claude stop other-app:work >out.txt 2>&1"), BLOCK)
check("only a redirection named", shell("claude stop 2>&1"), BLOCK)
check("claude attach by part of a name", shell("claude attach other"), BLOCK)
check("claude respawn, granted", shell("claude respawn allowed-app:work"), ALLOW)
check("claude respawn, not granted", shell("claude respawn other-app:work"), BLOCK)
check("claude.exe by path in PowerShell", shell('& "C:/tools/claude.exe" stop other-app:work', tool="PowerShell"),
      BLOCK)
check("claude stop inside bash -c", shell("bash -c 'claude stop other-app:work'"), BLOCK)
check("claude --bg here, not granted", shell('claude --bg "fix the build"'), BLOCK)
check("claude --bg after cd to a granted project",
      shell('cd "%s" && claude --bg "fix the build"' % _slash(ALLOWED)), ALLOW)
check("claude --bg after a cd the guard can't follow", shell('cd "$WORK" && claude --bg "fix the build"'), BLOCK)
check("a cd in a pipeline doesn't carry", shell('cd "%s" | claude --bg "x"' % _slash(ALLOWED)), BLOCK)
check("claude --bg --resume of a session elsewhere",
      shell('cd "%s" && claude --bg --resume other-app:work' % _slash(ALLOWED)), BLOCK)
check("claude -p is a one-shot here, not a session command", shell('claude -p "please stop the build"'), ALLOW)
check("text that mentions claude stop", shell("echo claude stop other-app:work"), ALLOW)

# --- SendMessage --------------------------------------------------------------------------------------------
check("SendMessage to a granted session", send("allowed-app:work"), ALLOW)
check("SendMessage to a session the grants don't cover", send("other-app:work"), BLOCK)
check("SendMessage to everyone", send("*"), BLOCK)
check("SendMessage to an unlisted session address", send("ghost-app:task"), BLOCK)
check("SendMessage to an own subagent by id", send("a" + "0123456789abcdef"), ALLOW)
check("SendMessage to an own teammate by name", send("researcher"), ALLOW)
check("SendMessage to an address, the list can't be read", send("allowed-app:work"), BLOCK, env=ENV_NO_LIST)
check("SendMessage to a teammate, the list can't be read", send("researcher"), ALLOW, env=ENV_NO_LIST)

# --- failures: closed on protected names, open otherwise -------------------------------------------------
check("broken input naming the grants file", None, BLOCK, raw='{"tool_name": "Write", grants.toml')
check("broken input otherwise", None, ALLOW, raw='{"tool_name": "Write", not json')
check("empty input", None, ALLOW, raw="")
check("a list instead of an object", None, ALLOW, raw="[1, 2]")


def _load():
    spec = importlib.util.spec_from_file_location("grants_guard_under_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def in_process(payload, fail=True):
    mod = _load()
    if fail:
        def boom(_payload):
            raise RuntimeError("checker broke")
        mod.check = boom
    old_in, old_err = sys.stdin, sys.stderr
    sys.stdin, sys.stderr = io.StringIO(json.dumps(payload)), io.StringIO()
    try:
        return mod.main()
    finally:
        sys.stdin, sys.stderr = old_in, old_err


if in_process(edit("Write", GRANTS)) != BLOCK:
    failures.append("a checker failure on a call naming the grants file must block")
if in_process(shell("gnomonkit setup security")) != BLOCK:
    failures.append("a checker failure on a protected subcommand must block")
if in_process(edit("Write", os.path.join(PROJECT, "README.md"))) != ALLOW:
    failures.append("a checker failure on an unrelated call must let it through")
if in_process(send("researcher")) != BLOCK:
    failures.append("a checker failure on a SendMessage must block")
if in_process(shell("claude stop allowed-app:work")) != BLOCK:
    failures.append("a checker failure on a session command must block")
if in_process(shell("git status")) != ALLOW:
    failures.append("a checker failure on an ordinary command must let it through")

if failures:
    print("\n".join("FAIL " + f for f in failures))
    sys.exit(1)
print("grants guard: all cases pass")
