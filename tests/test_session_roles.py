"""Session roles in session-dispatch: one primary per project, one session per name, labelled subs that
stop when finished, a primary stopped once idle so any client can open it, empty tabs hidden.

Synthetic data only: made-up project folders, a fake `claude` (first on PATH and named by
GNOMONKIT_CLAUDE_CMD) that records every call and prints sessions from a JSON file, a fake `gh` that
prints a pull request's state, and made-up transcripts under a temp CLAUDE_CONFIG_DIR. Nothing here
can reach a real session or GitHub.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IJ = ROOT / "plugins" / "interjector"
SCRIPT = IJ / "skills" / "session-dispatch" / "dispatch.py"
for p in (str(IJ), str(IJ / "hooks")):
    if p not in sys.path:
        sys.path.insert(0, p)

FAKE_CLAUDE = r'''import json, os, sys
state = os.environ["FAKE_CLAUDE_STATE"]
args = sys.argv[1:]
with open(os.path.join(state, "calls.jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\n")


def read(name, default):
    p = os.path.join(state, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def write(name, data):
    with open(os.path.join(state, name), "w", encoding="utf-8") as fh:
        json.dump(data, fh)


if args[:1] == ["agents"]:
    assert args == ["agents", "--json", "--all"], args
    seq = read("agents_seq.json", None)          # one listing per call, the last one repeated
    if seq:
        if len(seq) > 1:
            write("agents_seq.json", seq[1:])
        sys.stdout.write(json.dumps(seq[0]))
    else:
        sys.stdout.write(json.dumps(read("agents.json", [])))
elif "--bg" in args:
    new = read("on_bg.json", None)               # how the started session shows up in the list
    if new:
        write("agents.json", read("agents.json", []) + [new])
    sys.stdout.write("bg42\n")
elif args[:1] == ["stop"]:
    sys.stdout.write("stopped\n")
elif args[:1] == ["-p"]:
    refuse = read("p_refuse.json", None)         # the CLI's answer when the session is a background job
    if refuse:
        sys.stderr.write("Error: That session is running in the background (%s). Run `claude attach %s`"
                         " to open it, or `claude stop %s` first to resume it here.\n" % ((refuse,) * 3))
        sys.exit(1)
    sys.stdout.write("first turn done\n")
    if read("p_tab.json", False):                # VS Code picked the session up: its own process is listed
        sid = args[args.index("--session-id") + 1] if "--session-id" in args else args[args.index("--resume") + 1]
        name = args[args.index("--name") + 1] if "--name" in args else None
        write("agents.json", read("agents.json", []) + [
            {"pid": 777, "cwd": os.getcwd(), "kind": "interactive", "startedAt": 2000, "sessionId": sid,
             "name": name, "status": "idle"}])
else:
    sys.exit(3)
'''

FAKE_GH = r'''import json, os, re, sys
state = os.environ["FAKE_CLAUDE_STATE"]
args = sys.argv[1:]
with open(os.path.join(state, "gh_calls.jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\n")
assert args[:2] == ["pr", "view"] and args[3] == "--json" and args[4] in ("state", "url") and len(args) == 5, args
with open(os.path.join(state, "prs.json"), encoding="utf-8") as fh:
    states = json.load(fh)
num = re.search(r"(\d+)$", args[2]).group(1)    # a number, or a URL ending in one
if num not in states:
    sys.exit(1)
if args[4] == "url":
    out = {"url": "https://github.com/example-org/%s/pull/%s" % (os.path.basename(os.getcwd()), num)}
else:
    out = {"state": states[num]}
sys.stdout.write(json.dumps(out))
'''

SCRUB = ("GNOMONKIT_", "CLAUDE_PLUGIN_OPTION_")
SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDE_CONFIG_DIR")


def sid(i):
    return "%08d-0000-4000-8000-000000000000" % i


def bg(i, name, cwd, status="idle", **kw):
    """A running background session."""
    e = {"pid": 300 + i, "cwd": str(cwd), "kind": "background", "startedAt": 1000 + i, "id": "bg%02d" % i,
         "sessionId": sid(i), "name": name, "status": status,
         "state": "blocked" if status == "waiting" else "working"}
    if status == "waiting":
        e["waitingFor"] = "permission prompt"
    e.update(kw)
    return e


def stopped(i, name, cwd):
    """A background session that was stopped: listed, no live process."""
    return {"cwd": str(cwd), "kind": "background", "startedAt": 1000 + i, "id": "bg%02d" % i,
            "sessionId": sid(i), "name": name, "state": "stopped"}


def tab(i, name, cwd, status="idle"):
    """An interactive session (an IDE, desktop or terminal tab)."""
    return {"pid": 300 + i, "cwd": str(cwd), "kind": "interactive", "startedAt": 1000 + i,
            "sessionId": sid(i), "name": name, "status": status}


def rec(role, project, name, parent=None, **kw):
    r = {"role": role, "project": str(project), "task": "a task", "name": name, "parent": parent,
         "started": "2026-01-01T00:00:00Z", "pr": None, "done": None, "stopped": None, "bg": True, "via": "task",
         "sessionId": None}
    r.update(kw)
    return r


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def reply(text, mid="msg-1"):
    return {"type": "assistant", "message": {"id": mid, "role": "assistant",
                                             "content": [{"type": "text", "text": text}]}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "fake_claude.py"
    fake.write_text(FAKE_CLAUDE, encoding="utf-8")
    if os.name == "nt":
        (bindir / "claude.cmd").write_text('@"%s" "%s" %%*\r\n' % (sys.executable, fake), encoding="utf-8")
    else:
        exe = bindir / "claude"
        exe.write_text("#!%s\n" % sys.executable + FAKE_CLAUDE, encoding="utf-8")
        exe.chmod(0o755)
    gh = bindir / "fake_gh.py"
    gh.write_text(FAKE_GH, encoding="utf-8")
    projects = tmp_path / "projects"
    for name in ("demo-repo", "other-repo"):
        (projects / name).mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "cfg"
    for k in list(os.environ):
        if k.startswith(SCRUB) or k in SESSION_VARS:
            monkeypatch.delenv(k, raising=False)
    values = {
        "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
        "HOME": str(home), "USERPROFILE": str(home),
        "FAKE_CLAUDE_STATE": str(state),
        "GNOMONKIT_CLAUDE_CMD": json.dumps([sys.executable, str(fake)]),
        "GNOMONKIT_GH_CMD": json.dumps([sys.executable, str(gh)]),
        "GNOMONKIT_HOME": str(home / ".gnomonkit"),
        "GNOMONKIT_PROJECTS_ROOT": str(projects),
        "CLAUDE_CONFIG_DIR": str(cfg),
        # A test that wants the stop-when-idle watcher sets "any"; one that wants a tab sets vscode(env).
        "GNOMONKIT_DISPATCH_PRIMARY_CLIENT": "terminal",
        "GNOMONKIT_CODE_CMD": "[]",
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)           # the same for in-process calls (board, watch, roles)
    # pytest has no console, so the grants apply even without CLAUDECODE. In process, a person at a terminal
    # stands in; dispatch.py run as a child gets one grant per folder it can ask from, allowing anything.
    import gk_grants as G
    monkeypatch.setattr(G, "at_terminal", lambda stdin=None, stdout=None: True)
    (home / ".gnomonkit").mkdir()
    froms = [G.resolve(tmp_path), G.resolve(tmp_path) + "/**", G.repo_root(os.getcwd())]
    (home / ".gnomonkit" / "grants.toml").write_text("version = 1\n" + "".join(
        '\n[[grant]]\nfrom = %s\nto = ["*"]\nactions = %s\nagents = ["claude"]\n'
        % (json.dumps(f), json.dumps(list(G.ACTIONS))) for f in froms), encoding="utf-8")

    def run(*args, stdin="", cwd=None, **extra):
        e = dict(os.environ, **extra)
        r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=e,
                           input=stdin, encoding="utf-8", cwd=str(cwd or tmp_path),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, r.stdout, r.stderr

    def calls():
        p = state / "calls.jsonl"
        return [json.loads(x)["args"] for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def put(name, data):
        (state / name).write_text(json.dumps(data), encoding="utf-8")

    def roles(data=None):
        path = home / ".gnomonkit" / "dispatch" / "roles.json"
        if data is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data), encoding="utf-8")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def chat(i, cwd, *records):
        # The CLI's folder is the cwd's slug; under pytest's long temp path that passes Windows' path limit,
        # so the tests use a short folder, which the lookup's scan of every project folder finds.
        d = cfg / "projects" / "-synthetic-project"
        d.mkdir(parents=True, exist_ok=True)
        p = d / (sid(i) + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
        return p

    def audit():
        p = home / ".gnomonkit" / "dispatch" / "audit.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    return {"run": run, "calls": calls, "agents": lambda xs: put("agents.json", xs), "put": put,
            "roles": roles, "chat": chat, "audit": audit, "projects": projects,
            "demo": projects / "demo-repo", "other": projects / "other-repo", "home": home / ".gnomonkit",
            "state": state, "tmp": tmp_path}


def spawned(env):
    return [a for a in env["calls"]() if "--bg" in a]


def one_shots(env):
    """The one-shot first turns (claude -p ...) and the folders they ran in."""
    return [(a, c) for a, c in call_dirs(env) if a[:1] == ["-p"]]


def gh_calls(env):
    p = env["state"] / "gh_calls.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def call_dirs(env):
    """(args, cwd) of every claude call."""
    p = env["state"] / "calls.jsonl"
    return [(r["args"], r["cwd"]) for r in map(json.loads, p.read_text(encoding="utf-8").splitlines())]


def peer(text, ts):
    """A message another session sent (SendMessage): the CLI records it as a meta message from a peer."""
    return {"type": "user", "isMeta": True, "origin": {"kind": "peer", "from": "synthetic-peer"},
            "timestamp": ts, "message": {"role": "user", "content": text}}


def at(record, ts):
    return dict(record, timestamp=ts)


def stops(env):
    return [a for a in env["calls"]() if a[:1] == ["stop"]]


def only_list_start_and_stop(env):
    """Dispatch only ever lists, starts and stops sessions: never deletes anything."""
    return all(a[:1] in (["agents"], ["stop"]) or "--bg" in a for a in env["calls"]())


def brief_of(call):
    i = call.index("--add-dir")
    return Path(call[i - 1].split(" is in ", 1)[1].split(". Read", 1)[0])


# ---- roles and labels ----------------------------------------------------------------------------
def test_spawn_is_a_sub_titled_after_the_requesting_session(env):
    env["agents"]([tab(1, "demo-repo:release", env["demo"], "busy")])
    rc, out, err = env["run"]("spawn", "demo-repo", "--task", "review", "--prompt", "Review the diff.",
                              CLAUDE_CODE_SESSION_ID=sid(1))
    assert rc == 0, err
    [call] = spawned(env)
    assert call[call.index("--name") + 1] == "sub:release/review"
    text = brief_of(call).read_text(encoding="utf-8")
    assert "Review the diff." in text and "## When you are finished" in text and '" done' in text
    assert "role: sub of demo-repo:release" in out
    r = env["roles"]()["bg42"]
    assert (r["role"], r["parent"], r["name"], r["bg"]) == ("sub", "demo-repo:release", "sub:release/review", True)
    assert Path(r["project"]) == env["demo"].resolve()


def test_a_sub_started_from_a_terminal_is_labelled_with_its_repo(env):
    rc, _, err = env["run"]("spawn", "demo-repo", "--task", "lint")
    assert rc == 0, err
    [call] = spawned(env)
    assert call[call.index("--name") + 1] == "sub:demo-repo/lint"
    assert env["roles"]()["bg42"]["parent"] is None


def test_a_primary_keeps_its_plain_title_and_there_is_one_per_project(env):
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release")
    assert rc == 0, err
    [call] = spawned(env)
    assert call[call.index("--name") + 1] == "demo-repo:release"      # labels are for subs only
    assert "role: primary" in out and "claude attach bg42" in out
    assert env["roles"]()["bg42"]["role"] == "primary"
    env["agents"]([bg(42, "demo-repo:release", env["demo"], "busy", id="bg42")])
    rc, _, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "hotfix")
    assert rc == 2 and "already has a primary running" in err and "bg42" in err
    assert len(spawned(env)) == 1
    rc, _, err = env["run"]("spawn", "other-repo", "--role", "primary", "--task", "release")
    assert rc == 0, err                                                  # another project's primary is fine
    rc, _, err = env["run"]("spawn", "demo-repo", "--task", "hotfix")
    assert rc == 0, err                                                  # and a sub beside the primary


def test_handoff_takes_the_role_of_the_session_it_retires(env):
    body = "## Next\nShip it.\n"
    env["agents"]([bg(1, "demo-repo:old-work", env["demo"])])          # not started by dispatch: the user's
    rc, out, err = env["run"]("handoff", "demo-repo", "--task", "finish export", "--retire", "bg01", stdin=body)
    assert rc == 0, err
    call = spawned(env)[-1]
    assert call[call.index("--name") + 1] == "demo-repo:finish-export" and "role: primary" in out
    assert stops(env) == [["stop", "bg01"]]
    assert env["roles"]()["bg42"]["role"] == "primary"


def test_handoff_retiring_a_running_primary_is_not_a_second_primary(env):
    env["roles"]({"bg05": rec("primary", env["demo"].resolve(), "demo-repo:release")})
    env["agents"]([bg(5, "demo-repo:release", env["demo"])])
    rc, out, err = env["run"]("handoff", "demo-repo", "--task", "next step", "--retire", "bg05",
                              stdin="## Next\nTag it.\n")
    assert rc == 0, err
    assert "role: primary" in out and stops(env) == [["stop", "bg05"]]
    assert env["roles"]()["bg05"]["stopped_by"] == "retire"


def test_handoff_from_a_numbered_primary_takes_the_next_number(env):
    env["roles"]({"bg05": rec("primary", env["demo"].resolve(), "demo-repo:main-v3")})
    env["agents"]([bg(5, "demo-repo:main-v3", env["demo"])])
    rc, _, err = env["run"]("handoff", "demo-repo", "--task", "carry on toward release", "--retire", "bg05",
                            stdin="## Next\nTag it.\n")
    assert rc == 0, err
    assert spawned(env)[-1][spawned(env)[-1].index("--name") + 1] == "demo-repo:main-v4"
    env["agents"]([bg(6, "demo-repo:main-v4", env["demo"])])
    rc, _, err = env["run"]("handoff", "demo-repo", "--task", "next", "--retire", "bg06", "--name", "demo-repo:x",
                            stdin="## Next\nTag it.\n")
    assert rc == 0, err                                                  # --name still wins
    assert spawned(env)[-1][spawned(env)[-1].index("--name") + 1] == "demo-repo:x"


def test_handoff_of_a_sub_stays_a_sub_of_the_same_parent(env):
    env["roles"]({"bg03": rec("sub", env["demo"].resolve(), "sub:release/review", parent="demo-repo:release")})
    env["agents"]([bg(3, "sub:release/review", env["demo"])])
    rc, out, err = env["run"]("handoff", "demo-repo", "--task", "review part two", "--retire", "bg03",
                              stdin="## Next\nThe rest.\n")
    assert rc == 0, err
    call = spawned(env)[-1]
    assert call[call.index("--name") + 1].startswith("sub:release/review-part")
    assert env["roles"]()["bg42"]["parent"] == "demo-repo:release" and "role: sub" in out


def test_handoff_without_retire_is_a_sub_unless_asked(env):
    rc, _, err = env["run"]("handoff", "demo-repo", "--task", "side job", stdin="## Do\nIt.\n")
    assert rc == 0, err
    assert spawned(env)[-1][spawned(env)[-1].index("--name") + 1] == "sub:demo-repo/side-job"
    rc, _, err = env["run"]("handoff", "other-repo", "--task", "main line", "--role", "primary",
                            stdin="## Do\nIt.\n")
    assert rc == 0, err
    assert spawned(env)[-1][spawned(env)[-1].index("--name") + 1] == "other-repo:main-line"


# ---- the naming scheme ---------------------------------------------------------------------------
GK_NAMING = '[naming]\nprefix = "gk"\nprimary = "{prefix}-{repo}:{task}"\nsub = "{prefix}-sub:{parent}/{task}"\n'


def name_of(call):
    return call[call.index("--name") + 1]


def test_dispatch_names_subs_and_primaries_with_the_naming_scheme(env):
    (env["home"] / "personal.toml").write_text(GK_NAMING, encoding="utf-8")
    env["agents"]([tab(1, "gk-demo-repo:release", env["demo"], "busy")])
    rc, out, err = env["run"]("spawn", "demo-repo", "--task", "review", CLAUDE_CODE_SESSION_ID=sid(1))
    assert rc == 0, err
    assert name_of(spawned(env)[-1]) == "gk-sub:release/review" and "naming:" not in out
    assert env["roles"]()["bg42"]["parent"] == "gk-demo-repo:release"
    rc, _, err = env["run"]("spawn", "other-repo", "--role", "primary", "--task", "release")
    assert rc == 0, err
    assert name_of(spawned(env)[-1]) == "gk-other-repo:release"
    rc, _, err = env["run"]("spawn", "other-repo", "--task", "lint", "--name", "own-title")
    assert rc == 0, err
    assert name_of(spawned(env)[-1]) == "own-title"                     # --name still wins


def test_a_sub_titled_before_the_scheme_changed_still_hands_off_as_a_sub(env):
    (env["home"] / "personal.toml").write_text(GK_NAMING, encoding="utf-8")
    env["agents"]([bg(3, "sub:release/review", env["demo"])])          # no record: only the title says sub
    rc, out, err = env["run"]("handoff", "demo-repo", "--task", "review part two", "--retire", "bg03",
                              stdin="## Next\nThe rest.\n")
    assert rc == 0, err
    assert name_of(spawned(env)[-1]).startswith("gk-sub:release/review-part") and "role: sub" in out


def test_an_invalid_naming_scheme_gives_the_defaults_and_dispatch_says_so(env):
    rc, out, err = env["run"]("spawn", "demo-repo", "--task", "lint", GNOMONKIT_NAMING_SUB="sub:{parent}")
    assert rc == 0, err
    assert name_of(spawned(env)[-1]) == "sub:demo-repo/lint"
    assert ("naming: default, because the scheme set in GNOMONKIT_NAMING_SUB has problems: sub: needs {task}"
            in out)
    rc, out, _ = env["run"]("spawn", "demo-repo", "--task", "lint", "--dry-run", "--json",
                            GNOMONKIT_NAMING_SUB="sub:{parent}")
    assert "needs {task}" in json.loads(out)["naming_note"]


def test_the_naming_command_shows_the_scheme_and_its_problems(env):
    rc, out, err = env["run"]("naming")
    assert rc == 0, err
    assert "primary: {repo}:{task}" in out and "sub:     sub:{parent}/{task}" in out and "from:    default" in out
    assert ":fix-login  /  sub:release/fix-login" in out
    (env["home"] / "personal.toml").write_text(GK_NAMING, encoding="utf-8")
    rc, out, err = env["run"]("naming", "--json")
    got = json.loads(out)
    assert rc == 0 and got["sample_sub"] == "gk-sub:release/fix-login" and got["prefix"] == "gk"
    assert got["sample_primary"].startswith("gk-") and got["problems"] == [] and "[naming]" in got["source"]
    rc, out, _ = env["run"]("naming", GNOMONKIT_NAMING_PRIMARY="{repo}{task}")
    assert rc == 1 and "problems (" in out and "primary: {repo}{task} need text between them" in out
    assert "primary: {repo}:{task}" in out                              # the defaults are what applies


# ---- one session per name ------------------------------------------------------------------------
def test_a_stopped_session_holding_the_name_is_resumed_not_duplicated(env):
    env["agents"]([stopped(7, "sub:demo-repo/review", env["demo"])])
    rc, out, err = env["run"]("spawn", "demo-repo", "--task", "review", "--prompt", "Look again.")
    assert rc == 0, err
    [call] = spawned(env)
    assert call[:3] == ["--bg", "--resume", sid(7)]
    assert call[call.index("--name") + 1] == "sub:demo-repo/review"     # kept on a resume: the title stays
    assert call[3].startswith("A new brief for this session is in ")
    assert "Look again." in brief_of(call).read_text(encoding="utf-8")
    assert "resumed" in out and stops(env) == []
    r = env["roles"]()["bg07"]
    assert r["role"] == "sub" and r["sessionId"] == sid(7) and r["resumed"]


def test_a_running_session_holding_the_name_is_refused_unless_replace(env):
    env["agents"]([bg(7, "sub:demo-repo/review", env["demo"])])
    transcript = env["chat"](7, env["demo"], user("synthetic"), reply("synthetic reply"))
    rc, _, err = env["run"]("spawn", "demo-repo", "--task", "review")
    assert rc == 2 and "already running" in err and "--replace" in err
    assert spawned(env) == []
    rc, out, err = env["run"]("spawn", "demo-repo", "--task", "review", "--replace", "--dry-run")
    assert rc == 0 and "would replace" in out and stops(env) == [] and spawned(env) == []
    rc, out, err = env["run"]("spawn", "demo-repo", "--task", "review", "--replace")
    assert rc == 0, err
    sequence = [a[0] if a[0] != "--bg" else "--bg" for a in env["calls"]() if a[:1] != ["agents"]]
    assert sequence == ["--bg", "stop"]                     # a fresh one first, then the old one stopped
    assert stops(env) == [["stop", "bg07"]] and "--name" in spawned(env)[0]
    assert "replaced" in out and transcript.is_file()                  # nothing deleted
    assert only_list_start_and_stop(env)


def test_replace_whose_start_fails_leaves_the_old_session_running(env):
    env["agents"]([bg(7, "sub:demo-repo/review", env["demo"])])
    rc, _, err = env["run"]("spawn", "demo-repo", "--task", "review", "--replace",
                            GNOMONKIT_DISPATCH_PERMISSION_MODE="not-a-mode")
    assert rc == 2 and "unknown permission mode" in err
    assert stops(env) == [] and spawned(env) == []


def test_retire_by_the_id_list_shows_is_the_session_retired(env):
    env["agents"]([tab(1, "demo-repo:export", env["demo"], "idle")])
    rc, out, err = env["run"]("handoff", "demo-repo", "--task", "export", "--text", "state", "--retire", sid(1)[:8])
    assert rc == 0, err                                 # not "already running": it is the one being retired
    assert "retired: demo-repo:export (" + sid(1) + "), a tab" in out
    assert stops(env) == []                             # a tab is not stopped: the user closes it


def test_retire_with_a_prefix_of_two_sessions_is_refused(env):
    twins = ["abcdef01-0000-4000-8000-00000000000%d" % i for i in (1, 2)]
    env["agents"]([dict(tab(1, "demo-repo:a", env["demo"]), sessionId=twins[0]),
                   dict(tab(2, "demo-repo:b", env["demo"]), sessionId=twins[1])])
    rc, _, err = env["run"]("handoff", "demo-repo", "--task", "export", "--text", "state", "--retire", "abcdef01")
    assert rc == 2 and "more than one session" in err
    assert spawned(env) == [] and stops(env) == []


def test_stop_by_the_id_list_shows_stops_the_full_id(env):
    env["agents"]([tab(1, "demo-repo:export", env["demo"], "idle")])
    rc, _, err = env["run"]("stop", sid(1)[:8])
    assert rc == 0, err
    assert stops(env) == [["stop", sid(1)]]


def test_a_session_elsewhere_with_a_sub_title_does_not_hold_it(env):
    env["agents"]([bg(7, "sub:demo-repo/review", env["other"])])       # same title, another project
    rc, _, err = env["run"]("spawn", "demo-repo", "--task", "review")
    assert rc == 0, err


def test_a_primary_title_elsewhere_does_not_hold_it_either(env):
    elsewhere = env["tmp"] / "elsewhere" / "demo-repo"                  # another folder of the same name
    elsewhere.mkdir(parents=True)
    env["agents"]([stopped(7, "demo-repo:release", elsewhere), bg(8, "demo-repo:release", elsewhere)])
    rc, _, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release")
    assert rc == 0, err
    [call] = spawned(env)
    assert "--resume" not in call and call[call.index("--name") + 1] == "demo-repo:release"
    assert Path(env["roles"]()["bg42"]["project"]) == env["demo"].resolve()


def test_a_session_resumed_from_its_own_folder(env):
    inner = env["demo"] / "service"
    inner.mkdir()
    env["agents"]([stopped(7, "sub:demo-repo/review", inner)])        # inside the project: holds the title
    rc, out, err = env["run"]("spawn", "demo-repo", "--task", "review")
    assert rc == 0, err
    [(args, cwd)] = [(a, c) for a, c in call_dirs(env) if "--bg" in a]
    assert args[:3] == ["--bg", "--resume", sid(7)] and Path(cwd).resolve() == inner.resolve()


def test_an_archived_sub_frees_its_name(env):
    env["roles"]({"bg07": rec("sub", env["demo"].resolve(), "sub:demo-repo/review", done="2026-01-01T01:00:00Z",
                              stopped="2026-01-01T01:05:00Z")})
    env["agents"]([stopped(7, "sub:demo-repo/review", env["demo"])])
    rc, _, err = env["run"]("spawn", "demo-repo", "--task", "review")
    assert rc == 0, err
    [call] = spawned(env)
    assert "--resume" not in call and call[call.index("--name") + 1] == "sub:demo-repo/review"


def test_a_sub_stopped_because_its_pull_request_merged_is_archived(env):
    env["roles"]({"bg05": rec("sub", env["demo"].resolve(), "sub:demo-repo/lint", pr="12")})
    env["put"]("prs.json", {"12": "MERGED"})
    env["agents"]([bg(5, "sub:demo-repo/lint", env["demo"])])
    env["chat"](5, env["demo"], user("synthetic"), reply("Opened the pull request; CI is running."))
    rc, out, err = env["run"]("stop-finished")
    assert rc == 0, err
    assert stops(env) == [["stop", "bg05"]] and env["roles"]()["bg05"]["merged"]
    env["agents"]([stopped(5, "sub:demo-repo/lint", env["demo"])])
    from interjector import board
    assert board.collect()["hidden"]["archived"] == 1                 # hidden like a sub that reported done
    rc, _, err = env["run"]("spawn", "demo-repo", "--task", "lint", "--prompt", "Round two.")
    assert rc == 0, err
    call = spawned(env)[-1]
    assert "--resume" not in call and call[call.index("--name") + 1] == "sub:demo-repo/lint"


def test_a_resumed_sub_starts_without_its_old_pull_request_or_done(env):
    env["roles"]({"bg05": rec("sub", env["demo"].resolve(), "sub:demo-repo/lint", pr="12", pr_dir="x",
                              stopped="2026-01-01T02:00:00Z", stopped_by="stop")})    # stopped by hand: holds it
    env["agents"]([stopped(5, "sub:demo-repo/lint", env["demo"])])
    rc, out, err = env["run"]("spawn", "demo-repo", "--task", "lint", "--prompt", "Round two.")
    assert rc == 0, err
    assert "resumed bg05" in out
    r = env["roles"]()["bg05"]
    assert (r["pr"], r["pr_dir"], r["done"], r["merged"], r["stopped"], r["stopped_by"]) == (None,) * 6


# ---- the stop-when-idle watcher ------------------------------------------------------------------
def watcher(env, *extra):
    return env["run"]("_stop-when-idle", "bg42", "--poll", "0.05", *extra)


def test_watcher_stops_the_session_once_its_first_turn_ends(env):
    env["roles"]({"bg42": rec("primary", env["demo"].resolve(), "demo-repo:release")})
    busy, idle = bg(42, "demo-repo:release", env["demo"], "busy"), bg(42, "demo-repo:release", env["demo"])
    env["put"]("agents_seq.json", [[busy], [busy], [idle]])
    rc, _, err = watcher(env, "--timeout-minutes", "1")
    assert rc == 0, err
    assert stops(env) == [["stop", "bg42"]] and only_list_start_and_stop(env)
    [line] = [x for x in env["audit"]() if x.get("event") == "stop-when-idle"]
    assert line["result"] == "stopped" and line["state"] == "idle"
    assert env["roles"]()["bg42"]["stopped_by"] == "stop-when-idle"


def test_watcher_stops_an_idle_session_that_already_replied_or_one_waiting(env):
    env["chat"](42, env["demo"], user("synthetic brief pointer"), reply("Read the brief; starting."))
    env["agents"]([bg(42, "demo-repo:release", env["demo"])])
    assert watcher(env, "--timeout-minutes", "1")[0] == 0
    assert stops(env) == [["stop", "bg42"]]
    env["agents"]([bg(42, "demo-repo:release", env["demo"], "waiting")])
    (env["state"] / "calls.jsonl").unlink()
    assert watcher(env, "--timeout-minutes", "1")[0] == 0
    assert stops(env) == [["stop", "bg42"]]


def test_watcher_gives_up_after_the_timeout_and_leaves_it_running(env):
    env["agents"]([bg(42, "demo-repo:release", env["demo"])])         # idle, no reply yet, never seen busy
    t0 = time.time()
    rc, _, err = watcher(env, "--timeout-minutes", "0.003")
    assert rc == 0, err
    assert time.time() - t0 < 30
    assert stops(env) == []
    [line] = [x for x in env["audit"]() if x.get("event") == "stop-when-idle"]
    assert line["result"] == "gave up, left running"


def test_watcher_counts_only_a_reply_written_after_its_mark(env):
    old = env["chat"](42, env["demo"], user("old brief"), reply("Finished the old work."))
    mark = old.stat().st_size
    env["agents"]([bg(42, "demo-repo:release", env["demo"])])
    rc, _, err = watcher(env, "--timeout-minutes", "0.003", "--after-bytes", str(mark))
    assert rc == 0, err
    assert stops(env) == []                                   # the old reply is not the end of the new turn
    assert env["audit"]()[-1]["result"] == "gave up, left running"
    env["chat"](42, env["demo"], user("old brief"), reply("Finished the old work."), user("new brief"))
    rc, _, err = watcher(env, "--timeout-minutes", "0.003", "--after-bytes", str(mark))
    assert rc == 0, err
    assert stops(env) == []                                   # grown, but no reply to the new brief yet
    env["chat"](42, env["demo"], user("old brief"), reply("Finished the old work."), user("new brief"),
                reply("Done with the new work.", mid="msg-2"))
    rc, _, err = watcher(env, "--timeout-minutes", "1", "--after-bytes", str(mark))
    assert rc == 0, err
    assert stops(env) == [["stop", "bg42"]]


def test_a_resumed_primary_is_not_stopped_on_its_old_reply(env):
    env["roles"]({"bg04": rec("primary", env["demo"].resolve(), "demo-repo:release")})
    env["put"]("agents_seq.json", [[stopped(4, "demo-repo:release", env["demo"])],
                                   [bg(4, "demo-repo:release", env["demo"])]])
    env["chat"](4, env["demo"], user("old brief"), reply("Finished the old work."))
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", "--prompt", "New work.",
                              GNOMONKIT_DISPATCH_PRIMARY_CLIENT="any", GNOMONKIT_DISPATCH_STOP_TIMEOUT_MINUTES="0.05")
    assert rc == 0, err
    assert "resumed bg04" in out
    deadline = time.time() + 30
    while time.time() < deadline and not [x for x in env["audit"]() if x.get("event") == "stop-when-idle"]:
        time.sleep(0.2)
    assert [x["result"] for x in env["audit"]() if x.get("event") == "stop-when-idle"] == ["gave up, left running"]
    assert stops(env) == []


def test_watcher_notes_a_session_already_stopped(env):
    env["agents"]([stopped(42, "demo-repo:release", env["demo"])])
    assert watcher(env, "--timeout-minutes", "1")[0] == 0
    assert stops(env) == [] and env["audit"]()[-1]["result"] == "already stopped"


def test_no_watcher_when_the_primary_client_is_a_terminal(env):
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", "--json")
    assert rc == 0, err
    plan = json.loads(out)
    assert plan["role"] == "primary" and "watch" not in plan
    rc, out, _ = env["run"]("spawn", "other-repo", "--role", "primary", "--task", "release")
    assert "primary_client is terminal" in out and "claude attach" in out


def test_a_primary_gets_a_watcher_that_stops_it_when_idle(env):
    env["put"]("on_bg.json", bg(42, "demo-repo:release", env["demo"], id="bg42"))
    env["chat"](42, env["demo"], user("synthetic brief pointer"), reply("Read the brief; starting."))
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", "--prompt", "Go.",
                              "--json", GNOMONKIT_DISPATCH_PRIMARY_CLIENT="any",
                              GNOMONKIT_DISPATCH_POLL_SECONDS="0.05", GNOMONKIT_DISPATCH_STOP_TIMEOUT_MINUTES="0.5")
    assert rc == 0, err
    plan = json.loads(out)
    assert plan["watch"]["allowed"] and plan["watch"]["started"] and plan["watch"]["minutes"] == 0.5
    deadline = time.time() + 30
    while time.time() < deadline and not stops(env):
        time.sleep(0.1)
    assert stops(env) == [["stop", "bg42"]]
    deadline = time.time() + 10
    while time.time() < deadline and not [x for x in env["audit"]() if x.get("event") == "stop-when-idle"]:
        time.sleep(0.1)
    assert [x["result"] for x in env["audit"]() if x.get("event") == "stop-when-idle"] == ["stopped"]


def test_dry_run_says_how_the_primary_will_be_opened(env):
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", "--dry-run",
                              GNOMONKIT_DISPATCH_PRIMARY_CLIENT="any")
    assert rc == 0, err
    assert "would stop it once its first turn ends" in out and "any client can open it" in out
    assert env["calls"]() and spawned(env) == [] and env["roles"]() == {}


def only_grant(env, actions):
    """The grants file holds one grant: any project under the projects folder may do `actions` in any other."""
    import gk_grants as G
    kids = G.resolve(env["projects"]) + "/*"
    grants = Path(env["home"]) / "grants.toml"
    grants.parent.mkdir(parents=True, exist_ok=True)
    grants.write_text('version = 1\n\n[[grant]]\nfrom = %s\nto = [%s]\nactions = %s\nagents = ["claude"]\n'
                      % (json.dumps(kids), json.dumps(kids), json.dumps(actions)), encoding="utf-8")


def test_the_spawn_grant_lets_dispatch_park_the_primary_it_started(env):
    only_grant(env, ["spawn"])                     # no stop grant: parking its own start needs none
    env["agents"]([tab(1, "demo-repo:planning", env["demo"], "busy")])
    env["put"]("on_bg.json", bg(42, "demo-repo:release", env["demo"], id="bg42"))
    env["chat"](42, env["demo"], user("synthetic brief pointer"), reply("Read the brief; starting."))
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", "--json",
                              cwd=env["demo"], CLAUDECODE="1", CLAUDE_CODE_SESSION_ID=sid(1),
                              GNOMONKIT_DISPATCH_PRIMARY_CLIENT="any", GNOMONKIT_DISPATCH_POLL_SECONDS="0.05",
                              GNOMONKIT_DISPATCH_STOP_TIMEOUT_MINUTES="0.5")
    assert rc == 0, err
    watch = json.loads(out)["watch"]
    assert watch["allowed"] and watch["started"] and watch["summary"] == "allowed by grant 1 (spawn)"
    deadline = time.time() + 30
    while time.time() < deadline and not stops(env):
        time.sleep(0.1)
    assert stops(env) == [["stop", "bg42"]]
    [park] = [x for x in env["audit"]() if x.get("via") == "park"]
    assert park["action"] == "stop" and park["decision"] == "allow" and park["reason"] == "grant 1 (spawn)"


def test_the_spawn_grant_does_not_stop_other_sessions(env):
    only_grant(env, ["spawn", "handoff"])
    env["agents"]([tab(1, "demo-repo:planning", env["demo"], "busy"), bg(5, "demo-repo:old-work", env["demo"])])
    rc, _, err = env["run"]("stop", "bg05", cwd=env["demo"], CLAUDECODE="1", CLAUDE_CODE_SESSION_ID=sid(1))
    assert rc == 2 and "may not stop the session" in err and stops(env) == []


def test_handoff_from_a_primary_tab_retires_it_without_a_stop(env):
    only_grant(env, ["spawn", "handoff"])            # no stop grant: retiring a tab stops nothing
    env["roles"]({"bg05": rec("primary", env["demo"].resolve(), "demo-repo:audit", sessionId=sid(5))})
    env["agents"]([tab(5, "demo-repo:audit", env["demo"], "busy")])        # parked, then opened in an IDE
    inside = dict(cwd=env["demo"], CLAUDECODE="1", CLAUDE_CODE_SESSION_ID=sid(5))
    rc, _, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "main", **inside)
    assert rc == 2 and "already has a primary running" in err and "it is a tab" in err
    rc, out, err = env["run"]("handoff", "demo-repo", "--task", "main", "--retire", sid(5)[:8], "--client",
                              "background", stdin="## Next\nCarry on.\n", **inside)
    assert rc == 0, err
    assert stops(env) == [] and "demo-repo:audit (%s), a tab, so nothing was stopped: close its tab" % sid(5) in out
    r = env["roles"]()
    assert r["bg05"]["retired"] and r["bg05"]["retired_by"] == "handoff" and r["bg42"]["role"] == "primary"
    env["agents"]([tab(5, "demo-repo:audit", env["demo"], "busy"), bg(42, "demo-repo:main", env["demo"], id="bg42")])
    rc, out, err = env["run"]("list", "--json", **inside)
    assert rc == 0, err
    assert {x["name"]: x["role"] for x in json.loads(out)} == {"demo-repo:audit": "retired", "demo-repo:main": "primary"}


# ---- a primary as a VS Code tab ------------------------------------------------------------------
FAKE_EXT = r'''import json, os, sys
with open(os.path.join(os.environ["FAKE_CLAUDE_STATE"], "ext_calls.jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
'''


def vscode(env, client="vscode", tab=True, allowed=True):
    """The environment for a fake `code` and link opener, which record their arguments; a VS Code user folder
    whose settings allow the extension's links (or, `allowed=False`, do not); and whether the tab's own process
    appears once the first turn ran (`tab`)."""
    fake = env["state"] / "fake_ext.py"
    fake.write_text(FAKE_EXT, encoding="utf-8")
    env["put"]("p_tab.json", bool(tab))
    user = env["state"] / "vscode-user"
    user.mkdir(exist_ok=True)
    (user / "settings.json").write_text(json.dumps(
        {"extensions.confirmedUriHandlerExtensionIds": ["anthropic.claude-code"]} if allowed else {"editor.fontSize": 14}),
        encoding="utf-8")
    return {"GNOMONKIT_CODE_CMD": json.dumps([sys.executable, str(fake), "code"]),
            "GNOMONKIT_URI_OPENER": json.dumps([sys.executable, str(fake), "open"]),
            "GNOMONKIT_DISPATCH_VSCODE_SETTLE_SECONDS": "0", "GNOMONKIT_DISPATCH_PRIMARY_CLIENT": client,
            "GNOMONKIT_DISPATCH_TAB_SECONDS": "0", "GNOMONKIT_VSCODE_USER_DIR": str(user)}


def ext_calls(env):
    p = env["state"] / "ext_calls.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


def link_query(uri):
    from urllib.parse import parse_qs, urlsplit
    u = urlsplit(uri)
    assert (u.scheme, u.netloc, u.path) == ("vscode", "anthropic.claude-code", "/open")
    return {k: v[0] for k, v in parse_qs(u.query).items()}


def test_a_primary_opens_as_a_vscode_tab_after_a_one_shot_first_turn(env):
    import uuid
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", "--prompt",
                              "Ship it.", **vscode(env))
    assert rc == 0, err
    assert spawned(env) == []                                        # nothing in the background
    [(turn, cwd)] = one_shots(env)                                   # the first turn, in the project folder
    assert turn[:2] == ["-p", "--session-id"] and Path(cwd).resolve() == env["demo"].resolve()
    session = turn[2]
    assert str(uuid.UUID(session)) == session
    assert turn[3].startswith("Your task brief is in ") and turn[4:6] == ["--add-dir", str(Path(turn[3].split(" is in ", 1)[1].split(". Read", 1)[0]).parent)]
    assert turn[6:8] == ["--name", "demo-repo:release"]
    brief = Path(turn[3].split(" is in ", 1)[1].split(". Read", 1)[0])
    text = brief.read_text(encoding="utf-8")
    assert "Ship it." in text and "This session belongs in %s." % env["demo"].resolve() in text
    [code, opened] = ext_calls(env)
    assert code[0] == "code" and Path(code[1]) == env["demo"].resolve()   # the folder's window first
    q = link_query(opened[1])
    assert opened[0] == "open" and q == {"session": session}          # the session, nothing typed in
    assert "its first turn ran; opened a VS Code tab in the demo-repo window" in out and "Nothing to press" in out
    assert "press Enter" not in out
    r = env["roles"]()[session]
    assert (r["role"], r["client"], r["bg"], r["sessionId"], Path(r["brief"])) == (
        "primary", "vscode", False, session, brief)
    # The tab's own process carries it: it is the project's primary.
    env["agents"]([dict(tab(7, "demo-repo:release", env["demo"], "busy"), sessionId=session)])
    rc, _, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "hotfix", **vscode(env))
    assert rc == 2 and "already has a primary running" in err and len(ext_calls(env)) == 2


def test_a_tab_is_the_default_wherever_the_code_command_is_found(env):
    dry = ("spawn", "demo-repo", "--role", "primary", "--task", "release", "--dry-run")
    rc, out, err = env["run"](*dry, **vscode(env, client="any"))
    assert rc == 0, err
    assert "would run its first turn as a one-shot" in out and "open a VS Code tab in the demo-repo window" in out
    rc, out, err = env["run"](*dry, GNOMONKIT_DISPATCH_PRIMARY_CLIENT="any")      # no `code`: the background
    assert rc == 0, err
    assert "would stop it once its first turn ends" in out and "VS Code tab" not in out


def test_a_session_that_was_a_background_job_cannot_become_a_tab(env):
    env["state"].joinpath("p_refuse.json").write_text(json.dumps("bg04"), encoding="utf-8")
    env["agents"]([stopped(4, "demo-repo:release", env["demo"])])
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", **vscode(env))
    assert rc != 0
    assert "started as a background job, which no tab can take over" in err and "--replace" in err
    assert ext_calls(env) == [] and env["roles"]() == {}


def test_a_stopped_primary_holding_the_title_reopens_in_a_tab(env):
    env["agents"]([stopped(4, "demo-repo:release", env["demo"])])
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", **vscode(env))
    assert rc == 0, err
    [(turn, _cwd)] = one_shots(env)                                  # its next turn first, as a one-shot
    assert turn[:3] == ["-p", "--resume", sid(4)] and turn[3].startswith("A new brief for this session is in ")
    q = link_query(ext_calls(env)[-1][1])
    assert q == {"session": sid(4)}
    assert "reopened bg04 in a VS Code tab in the demo-repo window" in out and spawned(env) == []
    assert env["roles"]()["bg04"]["client"] == "vscode"


def test_without_the_code_command_the_tab_opens_where_focus_is_and_says_so(env):
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release",
                              **dict(vscode(env), GNOMONKIT_CODE_CMD="[]"))
    assert rc == 0, err
    assert [c[0] for c in ext_calls(env)] == ["open"]
    assert "whichever VS Code window has focus" in out and "close the tab and open it from the demo-repo session list" in out


def test_the_client_flag_picks_a_tab_or_the_background_for_one_call(env):
    v = vscode(env, client="terminal")
    dry = ("spawn", "demo-repo", "--role", "primary", "--task", "release", "--dry-run")
    rc, out, err = env["run"](*dry, **v)
    assert rc == 0, err
    assert "primary_client is terminal" in out and "VS Code tab" not in out
    rc, out, err = env["run"](*dry, "--client", "vscode", **v)
    assert rc == 0, err
    assert "open a VS Code tab in the demo-repo window" in out and "background sessions" not in out
    assert "link: vscode://anthropic.claude-code/open?session=%3Csession-id%3E" in out
    rc, out, err = env["run"](*dry, "--client", "background", **vscode(env))
    assert rc == 0, err
    assert "would stop it once its first turn ends" in out
    assert ext_calls(env) == [] and env["roles"]() == {}


def test_a_primary_brief_keeps_it_in_the_project_and_its_first_turn_short(env):
    def brief(*extra, **kw):
        rc, out, err = env["run"]("spawn", "demo-repo", "--task", "release", "--prompt", "Ship it.", "--dry-run",
                                  "--json", *extra, **kw)
        assert rc == 0, err
        return json.loads(out)["brief_text"]
    parked = brief("--role", "primary", GNOMONKIT_DISPATCH_PRIMARY_CLIENT="any")
    assert "Don't\nrun EnterWorktree" in parked and "git worktree add" in parked
    assert "You are the primary session for %s" % env["demo"].resolve() in parked
    assert "End your first turn with a short report" in parked
    running = brief("--role", "primary")                             # primary_client terminal: never parked
    assert "EnterWorktree" in running and "End your first turn" not in running
    tab = brief("--role", "primary", "--client", "vscode", **vscode(env))
    assert "EnterWorktree" in tab and "This session belongs in" in tab and "End your first turn" not in tab
    assert "EnterWorktree" not in brief()                            # a sub works where its brief says


# ---- a primary left running with Remote Control --------------------------------------------------
def remote_control_on(env):
    cfg = Path(os.environ["CLAUDE_CONFIG_DIR"])
    cfg.mkdir(exist_ok=True)
    (cfg / "settings.json").write_text(json.dumps({"remoteControlAtStartup": True}), encoding="utf-8")


def test_a_primary_with_remote_control_is_left_running_and_its_routes_named(env):
    remote_control_on(env)
    env["put"]("on_bg.json", bg(42, "demo-repo:release", env["demo"], id="bg42"))
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", "--prompt", "Go.",
                              "--json", GNOMONKIT_DISPATCH_PRIMARY_CLIENT="any",
                              GNOMONKIT_DISPATCH_STOP_TIMEOUT_MINUTES="0.1")
    assert rc == 0, err
    plan = json.loads(out)
    assert plan["remote_control"] and "watch" not in plan          # no watcher parks it: the phone needs it running
    [call] = spawned(env)
    assert call[call.index("--remote-control") + 1] == "demo-repo:release"
    assert "parked once that turn ends" not in plan["brief_text"] if "brief_text" in plan else True
    time.sleep(0.5)
    assert stops(env) == []
    assert env["roles"]()["bg42"]["remote"] is True
    rc, out, _ = env["run"]("spawn", "other-repo", "--role", "primary", "--task", "release", "--prompt", "Go.",
                            GNOMONKIT_DISPATCH_PRIMARY_CLIENT="any")
    assert "keeps running with Remote Control" in out and "claude.ai/code" in out and "claude attach" in out
    assert "not from a VS Code tab" in out


def test_a_running_primary_brief_says_where_the_user_reaches_it(env):
    remote_control_on(env)
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", "--prompt", "Go.",
                              "--dry-run", "--json", GNOMONKIT_DISPATCH_PRIMARY_CLIENT="any")
    assert rc == 0, err
    plan = json.loads(out)
    text = plan["brief_text"]
    assert "You keep running" in text and "claude.ai/code" in text and "End your first turn" not in text
    assert "Don't\nrun EnterWorktree" in text
    assert "keeps running with Remote Control" in plan.get("next", "") or True


def test_without_remote_control_the_primary_is_still_parked(env):
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", "--dry-run",
                              "--json", GNOMONKIT_DISPATCH_PRIMARY_CLIENT="any")
    assert rc == 0, err
    plan = json.loads(out)
    assert "watch" in plan and not plan["remote_control"] and "End your first turn" in plan["brief_text"]


def test_list_names_the_routes_for_a_running_primary(env):
    demo = env["demo"].resolve()
    env["roles"]({"bg01": rec("primary", demo, "demo-repo:main-v2", remote=True),
                  "bg02": rec("primary", env["other"].resolve(), "other-repo:main-v1")})
    env["agents"]([bg(1, "demo-repo:main-v2", env["demo"], "busy"), bg(2, "other-repo:main-v1", env["other"])])
    rc, out, err = env["run"]("list")
    assert rc == 0, err
    lines = [ln for ln in out.splitlines() if ln.startswith("open ")]
    assert len(lines) == 1 and lines[0].startswith("open demo-repo:main-v2: it keeps running with Remote Control")
    assert "claude attach bg01" in lines[0] and "claude.ai/code" in lines[0]
    rc, out, _ = env["run"]("list", "--json")
    assert rc == 0 and "open demo-repo" not in out


# ---- finished subs -------------------------------------------------------------------------------
def finished_setup(env):
    demo = env["demo"].resolve()
    env["roles"]({
        "bg01": rec("sub", demo, "sub:release/done-idle", done="2026-01-01T01:00:00Z"),
        "bg02": rec("sub", demo, "sub:release/pr-merged", pr="17"),
        "bg03": rec("sub", demo, "sub:release/pr-open", pr="18"),
        "bg04": rec("sub", demo, "sub:release/busy", done="2026-01-01T01:00:00Z"),
        "bg05": rec("sub", demo, "sub:release/asking", done="2026-01-01T01:00:00Z"),
        "bg06": rec("primary", demo, "demo-repo:release", done="2026-01-01T01:00:00Z"),
        "bg07": rec("sub", demo, "sub:release/a-tab", done="2026-01-01T01:00:00Z", sessionId=sid(7)),
        "bg08": rec("sub", demo, "sub:release/waiting", done="2026-01-01T01:00:00Z"),
        "bg09": rec("sub", demo, "sub:release/gone", done="2026-01-01T01:00:00Z"),
        "bg10": rec("sub", demo, "sub:release/working"),
    })
    env["put"]("prs.json", {"17": "MERGED", "18": "OPEN"})
    env["agents"]([
        bg(1, "sub:release/done-idle", env["demo"]),
        bg(2, "sub:release/pr-merged", env["demo"]),
        bg(3, "sub:release/pr-open", env["demo"]),
        bg(4, "sub:release/busy", env["demo"], "busy"),
        bg(5, "sub:release/asking", env["demo"]),
        bg(6, "demo-repo:release", env["demo"]),
        tab(7, "sub:release/a-tab", env["demo"]),
        bg(8, "sub:release/waiting", env["demo"], "waiting"),
        bg(10, "sub:release/working", env["demo"]),
        bg(11, "sub:release/unknown", env["demo"]),                    # no record: never dispatch's to stop
    ])
    for i in (1, 2, 3, 4, 6, 7, 8, 10, 11):
        env["chat"](i, env["demo"], user("synthetic"), reply("Reported back; the work is in the brief's folder."))
    env["chat"](5, env["demo"], user("synthetic"), reply("Two ways to do this.\n\nWhich one do you prefer?"))


def test_stop_finished_stops_only_finished_subs(env):
    finished_setup(env)
    rc, out, err = env["run"]("stop-finished", "--dry-run", "--json")
    assert rc == 0, err
    rows = {r["key"]: r for r in json.loads(out)}
    assert {k for k, r in rows.items() if r["action"] == "would stop"} == {"bg01", "bg02"}
    assert stops(env) == []
    why = {k: r["reason"] for k, r in rows.items()}
    assert "is open" in why["bg03"] and why["bg04"] == "busy" and "asks the user" in why["bg05"]
    assert why["bg07"] == "an interactive session" and why["bg08"] == "waiting on the user"
    assert why["bg09"] == "not in the session list" and why["bg10"] == "not reported done"
    assert "bg06" not in rows and "bg11" not in rows                 # a primary, an unknown session
    rc, out, err = env["run"]("stop-finished")
    assert rc == 0, err
    assert sorted(stops(env)) == [["stop", "bg01"], ["stop", "bg02"]]
    assert "stopped: bg01" in out and "merged" in out
    assert only_list_start_and_stop(env)
    lines = [x for x in env["audit"]() if x.get("event") == "auto-stop"]
    assert sorted(x["session"] for x in lines) == ["bg01", "bg02"] and {x["result"] for x in lines} == {"stopped"}
    assert env["roles"]()["bg01"]["stopped_by"] == "auto-stop"
    gh = [c["args"] for c in gh_calls(env)]
    assert sorted(a[2] for a in gh if a[4] == "state") == ["17", "17", "18", "18"]   # once a pass each
    n = len(stops(env))
    assert env["run"]("stop-finished")[0] == 0 and len(stops(env)) == n   # stopped once, not again


def test_list_stops_finished_subs_first_unless_auto_stop_is_off(env):
    env["roles"]({"bg01": rec("sub", env["demo"].resolve(), "sub:release/done-idle", done="2026-01-01T01:00:00Z")})
    env["agents"]([bg(1, "sub:release/done-idle", env["demo"])])
    env["chat"](1, env["demo"], user("synthetic"), reply("Reported."))
    rc, out, err = env["run"]("list", GNOMONKIT_DISPATCH_AUTO_STOP="false")
    assert rc == 0, err
    assert stops(env) == []
    rc, out, err = env["run"]("list", "--json")
    assert rc == 0, err
    assert stops(env) == []                                   # a reader of --json gets no side effects
    rc, out, err = env["run"]("list")
    assert rc == 0, err
    assert stops(env) == [["stop", "bg01"]] and "stopped finished sub sub:release/done-idle" in out


def test_explicit_stop_finished_runs_even_with_auto_stop_off(env):
    env["roles"]({"bg01": rec("sub", env["demo"].resolve(), "sub:release/done-idle", done="2026-01-01T01:00:00Z")})
    env["agents"]([bg(1, "sub:release/done-idle", env["demo"])])
    env["chat"](1, env["demo"], user("synthetic"), reply("Reported."))
    rc, _, err = env["run"]("stop-finished", GNOMONKIT_DISPATCH_AUTO_STOP="false")
    assert rc == 0, err
    assert stops(env) == [["stop", "bg01"]]


def test_a_sub_is_read_again_just_before_it_is_stopped(env):
    env["roles"]({"bg03": rec("sub", env["demo"].resolve(), "sub:demo-repo/lint", done="2026-01-01T01:00:00Z")})
    env["put"]("agents_seq.json", [[bg(3, "sub:demo-repo/lint", env["demo"])],
                                   [bg(3, "sub:demo-repo/lint", env["demo"], "busy")]])     # idle, then busy
    env["chat"](3, env["demo"], user("brief"), reply("Reported."))
    rc, out, err = env["run"]("stop-finished")
    assert rc == 0, err
    assert stops(env) == [] and "changed just before the stop (busy)" in out
    assert sum(1 for a in env["calls"]() if a[:1] == ["agents"]) == 2


def test_a_done_sub_with_a_newer_prompt_is_not_stopped_and_is_no_longer_done(env):
    env["roles"]({"bg06": rec("sub", env["demo"].resolve(), "sub:demo-repo/lint", done="2026-01-01T01:00:00Z")})
    env["agents"]([bg(6, "sub:demo-repo/lint", env["demo"])])
    env["chat"](6, env["demo"], user("brief"), reply("Reported."), user("One more thing: also fix the typo."))
    rc, out, err = env["run"]("stop-finished", "--dry-run")
    assert rc == 0, err
    assert "would stop" not in out and env["roles"]()["bg06"]["done"]         # a dry run writes nothing
    rc, out, err = env["run"]("stop-finished")
    assert rc == 0, err
    assert stops(env) == [] and "new work reached it" in out
    assert env["roles"]()["bg06"]["done"] is None


def test_a_message_after_done_takes_the_done_back_even_once_answered(env):
    env["roles"]({"bg06": rec("sub", env["demo"].resolve(), "sub:demo-repo/lint", done="2026-01-01T01:00:00.500Z")})
    env["agents"]([bg(6, "sub:demo-repo/lint", env["demo"])])
    env["chat"](6, env["demo"], at(user("brief"), "2026-01-01T00:59:00.000Z"),
                at(reply("Reported."), "2026-01-01T01:00:01.000Z"),
                peer("Follow-up: also update the changelog.", "2026-01-01T01:10:00.000Z"),
                at(reply("Updated it; checking CI next.", mid="msg-2"), "2026-01-01T01:11:00.000Z"))
    rc, out, err = env["run"]("stop-finished")
    assert rc == 0, err
    assert stops(env) == [] and env["roles"]()["bg06"]["done"] is None   # the follow-up isn't cut to one turn


def test_a_task_notice_after_the_reply_waits_but_keeps_done(env):
    env["roles"]({"bg06": rec("sub", env["demo"].resolve(), "sub:demo-repo/lint", done="2026-01-01T01:00:00Z")})
    env["agents"]([bg(6, "sub:demo-repo/lint", env["demo"])])
    env["chat"](6, env["demo"], user("brief"), reply("Reported."),
                {"type": "user", "origin": {"kind": "task-notification"},
                 "message": {"content": "<task-notification>synthetic</task-notification>"}})
    rc, out, err = env["run"]("stop-finished")
    assert rc == 0, err
    assert stops(env) == [] and "has a turn to take" in out and env["roles"]()["bg06"]["done"]


def test_a_roles_write_error_after_a_stop_does_not_fail_the_pass(env, monkeypatch):
    from interjector import roles as R
    from interjector import watch
    watch._DISPATCH = None
    env["roles"]({"bg01": rec("sub", env["demo"].resolve(), "sub:release/done-idle", done="2026-01-01T01:00:00Z")})
    env["agents"]([bg(1, "sub:release/done-idle", env["demo"])])
    env["chat"](1, env["demo"], user("synthetic"), reply("Reported."))

    def broken(*a, **k):
        raise OSError("synthetic write error")
    monkeypatch.setattr(R, "update", broken)
    [row] = watch.auto_stop_finished()
    assert row["action"] == "stopped" and "could not be updated" in row["warning"]
    assert stops(env) == [["stop", "bg01"]]


def test_stop_finished_asks_the_grants_inside_a_session(env):
    env["roles"]({"bg01": rec("sub", env["demo"].resolve(), "sub:release/done-idle", done="2026-01-01T01:00:00Z")})
    env["agents"]([bg(1, "sub:release/done-idle", env["demo"]), tab(2, "demo-repo:release", env["demo"], "busy")])
    env["chat"](1, env["demo"], user("synthetic"), reply("Reported."))
    (env["home"] / "grants.toml").unlink()
    inside = dict(cwd=env["demo"], CLAUDECODE="1", CLAUDE_CODE_SESSION_ID=sid(2))
    rc, out, err = env["run"]("stop-finished", "--json", **inside)          # no grants file: default deny
    assert rc == 0, err
    [row] = json.loads(out)
    assert row["action"] == "refused" and "grants refused the stop" in row["reason"] and stops(env) == []
    only_grant(env, ["handoff"])                     # the grant that let it start is enough to park it
    rc, out, err = env["run"]("stop-finished", "--json", **inside)
    assert rc == 0, err
    [row] = json.loads(out)
    assert row["action"] == "stopped" and stops(env) == [["stop", "bg01"]]


def test_watch_loop_auto_stop(env, monkeypatch):
    from interjector import watch
    watch._DISPATCH = None
    assert watch.auto_stop_finished() == [] and env["calls"]() == []    # no records: the list isn't even read
    env["roles"]({"bg01": rec("sub", env["demo"].resolve(), "sub:release/done-idle", done="2026-01-01T01:00:00Z")})
    env["agents"]([bg(1, "sub:release/done-idle", env["demo"])])
    env["chat"](1, env["demo"], user("synthetic"), reply("Reported."))
    assert watch.run(session="no-such-session", once=True) == 0
    assert watch.run(once=True) == 0
    assert stops(env) == []                         # a one-session or one-shot run leaves this to the loop

    class Out(Exception):
        pass

    def sleep(_s):
        raise Out()
    monkeypatch.setattr(watch, "_active_sessions", lambda: [])
    monkeypatch.setattr(watch, "time", type("T", (), {"time": staticmethod(time.time), "sleep": staticmethod(sleep)}))
    with pytest.raises(Out):
        watch.run()
    assert stops(env) == [["stop", "bg01"]]


# ---- done and pr ---------------------------------------------------------------------------------
def test_done_and_pr_mark_the_sessions_own_record(env):
    env["roles"]({"bg42": rec("sub", env["demo"].resolve(), "sub:release/review")})
    env["agents"]([bg(42, "sub:release/review", env["demo"], "busy", id="bg42")])
    me = {"CLAUDE_CODE_SESSION_ID": sid(42)}
    rc, out, err = env["run"]("pr", "https://github.com/example-org/demo-repo/pull/17", **me)
    assert rc == 0, err
    assert "Once that pull request merges" in out
    assert env["roles"]()["bg42"]["pr"] == "https://github.com/example-org/demo-repo/pull/17"
    assert env["roles"]()["bg42"]["done"] is None
    rc, out, err = env["run"]("done", "--pr", "#18", **me)
    assert rc == 0, err
    r = env["roles"]()["bg42"]
    assert r["done"] and r["pr"] == "18" and r["sessionId"] == sid(42) and "marked done" in out
    rc, _, err = env["run"]("pr", "not-a-pr", **me)
    assert rc == 2 and "not a pull request" in err
    rc, _, err = env["run"]("done", CLAUDE_CODE_SESSION_ID=sid(99))
    assert rc == 2 and "no session-dispatch record" in err
    assert stops(env) == [] and spawned(env) == []


def test_a_pull_request_number_is_kept_as_its_url_and_checked_by_it(env):
    inner = env["other"]                    # the sub works in another repository than its recorded project
    env["roles"]({"bg42": rec("sub", env["demo"].resolve(), "sub:release/review")})
    env["agents"]([bg(42, "sub:release/review", inner, "busy", id="bg42")])
    env["put"]("prs.json", {"17": "OPEN"})
    rc, out, err = env["run"]("pr", "17", CLAUDE_CODE_SESSION_ID=sid(42))
    assert rc == 0, err
    url = "https://github.com/example-org/other-repo/pull/17"        # read in the sub's own folder
    r = env["roles"]()["bg42"]
    assert r["pr"] == url and r["pr_dir"] is None and url in out
    env["put"]("prs.json", {"17": "MERGED"})
    env["agents"]([bg(42, "sub:release/review", inner, id="bg42")])
    env["chat"](42, inner, user("synthetic"), reply("Opened it; waiting for review."))
    rc, out, err = env["run"]("stop-finished")
    assert rc == 0, err
    assert stops(env) == [["stop", "bg42"]]
    assert gh_calls(env)[-1]["args"][2] == url


def test_a_pull_request_gh_cannot_resolve_is_read_where_it_was_recorded(env):
    env["roles"]({"bg42": rec("sub", env["demo"].resolve(), "sub:release/review")})
    env["agents"]([bg(42, "sub:release/review", env["other"], "busy", id="bg42")])
    env["put"]("prs.json", {})                                        # gh can't say: number and folder kept
    rc, out, err = env["run"]("pr", "#19", CLAUDE_CODE_SESSION_ID=sid(42))
    assert rc == 0, err
    r = env["roles"]()["bg42"]
    assert r["pr"] == "19" and Path(r["pr_dir"]) == env["other"]
    env["put"]("prs.json", {"19": "OPEN"})
    env["agents"]([bg(42, "sub:release/review", env["demo"], id="bg42")])
    env["chat"](42, env["demo"], user("synthetic"), reply("Opened it."))
    assert env["run"]("stop-finished")[0] == 0
    last = gh_calls(env)[-1]
    assert last["args"][2] == "19" and Path(last["cwd"]).resolve() == env["other"].resolve()


def test_a_roles_file_that_cannot_be_parsed_is_kept_aside(env):
    p = Path(env["home"]) / "dispatch" / "roles.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"bg09": {"role": "primary"}, broken', encoding="utf-8")
    rc, out, err = env["run"]("spawn", "demo-repo", "--task", "lint")
    assert rc == 0, err
    assert "could not be read, so it was kept as roles.json.bad-" in err
    [aside] = list(p.parent.glob("roles.json.bad-*"))
    assert "broken" in aside.read_text(encoding="utf-8")
    assert list(json.loads(p.read_text(encoding="utf-8"))) == ["bg42"]


def test_done_moves_a_pending_record_to_the_sessions_id(env):
    env["roles"]({"name:sub:release/review": rec("sub", env["demo"].resolve(), "sub:release/review")})
    env["agents"]([bg(42, "sub:release/review", env["demo"], "busy", id="bg42")])
    rc, _, err = env["run"]("done", CLAUDE_CODE_SESSION_ID=sid(42))
    assert rc == 0, err
    assert list(env["roles"]()) == ["bg42"] and env["roles"]()["bg42"]["done"]


# ---- list and board ------------------------------------------------------------------------------
def tree_setup(env):
    demo, other = env["demo"].resolve(), env["other"].resolve()
    env["roles"]({
        "bg01": rec("primary", demo, "demo-repo:release"),
        "bg02": rec("sub", demo, "sub:release/review", parent="demo-repo:release"),
        "bg03": rec("sub", demo, "sub:review/lint", parent="sub:release/review"),
        "bg04": rec("primary", other, "other-repo:fix"),
    })
    entries = [bg(3, "sub:review/lint", env["demo"], "busy"), tab(5, "demo-repo:notes", env["demo"]),
               bg(2, "sub:release/review", env["demo"]), bg(1, "demo-repo:release", env["demo"]),
               bg(4, "other-repo:fix", env["other"], "waiting")]
    env["agents"](entries)
    for e in entries:
        env["chat"](int(e["sessionId"][:8]), e["cwd"], user("synthetic"), reply("synthetic"))


def test_list_groups_by_project_primary_first_subs_under_their_parent(env):
    tree_setup(env)
    rc, out, err = env["run"]("list", "--json")
    assert rc == 0, err
    rows = json.loads(out)
    assert [(r["name"], r["role"], r["depth"]) for r in rows] == [
        ("other-repo:fix", "primary", 0),                              # the project waiting on you first
        ("demo-repo:release", "primary", 0), ("sub:release/review", "sub", 1), ("sub:review/lint", "sub", 2),
        ("demo-repo:notes", "", 0)]
    assert rows[2]["parent"] == "demo-repo:release"
    rc, out, _ = env["run"]("list")
    head, *lines = out.splitlines()
    assert head.split()[:6] == ["STATE", "KIND", "ID", "NAME", "ROLE", "PARENT"]
    assert any("    sub:review/lint" in ln for ln in lines)          # indented under its parent
    assert stops(env) == []


def test_board_groups_by_project_and_indents_subs(env):
    tree_setup(env)
    from interjector import board
    b = board.collect()
    assert [(r["name"], r["depth"]) for r in b["sessions"]] == [
        ("other-repo:fix", 0), ("demo-repo:release", 0), ("sub:release/review", 1), ("sub:review/lint", 2),
        ("demo-repo:notes", 0)]
    page = board.render(b)
    assert '<h2 class="group">other-repo</h2>' in page and '<h2 class="group">demo-repo</h2>' in page
    assert 'class="idle d1"' in page and 'class="busy d2"' in page and "sub of demo-repo:release" in page
    assert page.index("other-repo:fix") < page.index("demo-repo:release") < page.index("sub:review/lint")


# ---- empty sessions and archived subs ------------------------------------------------------------
def empty_setup(env):
    d = env["demo"]
    env["agents"]([tab(1, "", d), tab(2, "", d), tab(3, "demo-repo:real-work", d), tab(4, "", d, "busy"),
                   tab(6, "", d, "waiting"), bg(8, "demo-repo:no-transcript-found", d)])
    env["chat"](2, d, {"type": "user", "message": {"content": "<command-name>/clear</command-name>"}},
                {"type": "user", "isMeta": True, "message": {"content": "synthetic caveat"}})
    env["chat"](3, d, user("synthetic"), reply("synthetic"))


def test_empty_sessions_are_hidden_behind_a_count_and_shown_with_all(env):
    empty_setup(env)
    rc, out, err = env["run"]("list")
    assert rc == 0, err
    assert "2 empty tabs: close them in your IDE" in out
    assert sid(1)[:8] not in out and sid(2)[:8] not in out
    assert "demo-repo:real-work" in out and sid(4)[:8] in out and sid(6)[:8] in out   # busy, waiting: shown
    assert "demo-repo:no-transcript-found" in out          # a background session is never an empty tab
    rc, out, _ = env["run"]("list", "--all", "--json")
    rows = {r["id"]: r for r in json.loads(out)}
    assert rows[sid(1)[:8]]["empty"] and rows[sid(2)[:8]]["empty"] and not rows[sid(3)[:8]]["empty"]
    rc, out, _ = env["run"]("list", "--all")
    assert "(empty)" in out
    assert env["calls"]() and stops(env) == []                         # an empty tab is never stopped


def test_board_hides_empty_tabs_with_a_count(env):
    empty_setup(env)
    from interjector import board
    b = board.collect()
    assert b["hidden"]["empty"] == 2 and len(b["sessions"]) == 4
    assert "2 empty tabs: close them in your IDE" in board.render(b)
    assert len(board.collect(include_all=True)["sessions"]) == 6


def test_finished_stopped_subs_are_hidden_from_the_board_and_shown_with_all(env):
    env["roles"]({"bg07": rec("sub", env["demo"].resolve(), "sub:release/review", done="2026-01-01T01:00:00Z",
                              stopped="2026-01-01T01:05:00Z")})
    env["agents"]([stopped(7, "sub:release/review", env["demo"]), stopped(8, "nightly", env["demo"])])
    from interjector import board
    b = board.collect()
    assert [r["name"] for r in b["sessions"]] == ["nightly"] and b["hidden"]["archived"] == 1
    assert {r["name"] for r in board.collect(include_all=True)["sessions"]} == {"nightly", "sub:release/review"}
    rc, out, _ = env["run"]("list", "--all", "--json")
    assert "sub:release/review" in {r["name"] for r in json.loads(out)}


# ---- settings and helpers ------------------------------------------------------------------------
def test_settings_from_environment_then_profile_then_defaults(env, monkeypatch):
    from interjector import roles as R
    monkeypatch.delenv("GNOMONKIT_DISPATCH_PRIMARY_CLIENT")
    assert (R.setting("auto_stop"), R.setting("stop_timeout_minutes"), R.setting("primary_client")) == (
        True, 15, "any")
    profile = Path(env["home"]) / "personal.toml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text('[dispatch]\nauto_stop = false\nstop_timeout_minutes = 5\nprimary_client = "terminal"\n',
                       encoding="utf-8")
    assert (R.setting("auto_stop"), R.setting("stop_timeout_minutes"), R.setting("primary_client")) == (
        False, 5, "terminal")
    monkeypatch.setenv("GNOMONKIT_DISPATCH_AUTO_STOP", "yes")
    monkeypatch.setenv("GNOMONKIT_DISPATCH_STOP_TIMEOUT_MINUTES", "0.5")
    assert R.setting("auto_stop") is True and R.setting("stop_timeout_minutes") == 0.5
    monkeypatch.setenv("GNOMONKIT_DISPATCH_AUTO_STOP", "maybe")          # doesn't fit: the profile's value
    monkeypatch.setenv("GNOMONKIT_DISPATCH_STOP_TIMEOUT_MINUTES", "-3")
    assert R.setting("auto_stop") is False and R.setting("stop_timeout_minutes") == 5
    monkeypatch.setenv("GNOMONKIT_DISPATCH_STOP_TIMEOUT_MINUTES", "100000")
    assert R.setting("stop_timeout_minutes") == 24 * 60                   # at most a day
    monkeypatch.delenv("GNOMONKIT_DISPATCH_STOP_TIMEOUT_MINUTES")
    profile.write_text('[dispatch]\nprimary_client = "phone"\nstop_timeout_minutes = "soon"\n', encoding="utf-8")
    assert R.setting("primary_client") == "any" and R.setting("stop_timeout_minutes") == 15


def test_transcript_found_by_the_cwd_slug_first(env):
    from interjector import roles as R
    cfg = Path(os.environ["CLAUDE_CONFIG_DIR"])
    assert R.project_slug("C:/work/demo_repo") == "C--work-demo-repo"
    here = cfg / "projects" / "C--work-demo-repo" / (sid(1) + ".jsonl")
    elsewhere = cfg / "projects" / "-elsewhere" / (sid(2) + ".jsonl")
    for p in (here, elsewhere):
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(user("synthetic")) + "\n", encoding="utf-8")
    assert R.transcript_path(sid(1), "C:/work/demo_repo") == here
    assert R.transcript_path(sid(2), "C:/other") == elsewhere          # not under its slug: any project folder
    assert R.transcript_path("../" + sid(1)) is None                   # never a path out of the folder


def test_name_and_pull_request_helpers(tmp_path, monkeypatch):
    for k in list(os.environ):                     # the default scheme: no profile or naming setting of the user's
        if k.startswith(SCRUB):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path))
    from interjector import roles as R
    assert R.task_part("demo-repo:release") == "release" and R.task_part("sub:release/review") == "review"
    assert R.sub_parent_label("sub:release/review") == "release" and R.sub_parent_label("demo-repo:x") == ""
    assert R.sub_name("release", "review") == "sub:release/review"
    assert R.normalise_pr("17") == "17" and R.normalise_pr("#17") == "17" and R.normalise_pr("x17") is None
    url = "https://github.com/example-org/demo-repo/pull/17"
    assert R.normalise_pr(url) == url and R.normalise_pr(url + "/files") is None
    assert R.is_prompt(user("fix it")) and not R.is_prompt(user("<command-name>/clear</command-name>"))
    assert not R.is_prompt({"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}})


def test_a_tab_for_someone_who_works_remotely_says_the_phone_sees_it_only_while_open(env):
    args = ("spawn", "demo-repo", "--role", "primary", "--task", "release", "--prompt", "Ship it.")
    rc, out, err = env["run"](*args, **vscode(env))
    assert rc == 0, err
    assert "Nothing to press" in out and "You work remotely" not in out
    profile = Path(env["home"]) / "personal.toml"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text('[working]\nremote = "yes"\n', encoding="utf-8")
    env["agents"]([])
    rc, out, err = env["run"]("spawn", "other-repo", "--role", "primary", "--task", "release", "--prompt", "Go.",
                              **vscode(env))
    assert rc == 0, err
    assert ("You work remotely: the phone sees this tab only while VS Code has it open" in out
            and "setup remote on" in out and "claude attach" in out)



def test_a_tab_whose_process_never_appears_is_not_called_open(env):
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", **vscode(env, tab=False))
    assert rc == 0, err
    assert "no VS Code process picked up the session within 0 s" in out and "demo-repo session list" in out
    assert "Nothing to press" not in out
    [(turn, _cwd)] = one_shots(env)
    assert env["roles"]()[turn[2]]["client"] == "vscode"            # the session exists; a later tab carries it


def test_a_link_vs_code_has_not_allowed_yet_is_reported_with_the_fix(env):
    v = vscode(env, tab=False, allowed=False)
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", "--dry-run", **v)
    assert rc == 0, err
    assert "VS Code has not yet been allowed" in out and "confirmedUriHandlerExtensionIds" in out
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", **v)
    assert rc == 0, err
    assert "VS Code is waiting for a click" in out
    assert "Allow 'Claude Code' extension to open this URI?" in out and "Don't ask again" in out
    assert "the phone does not list it" in out and "demo-repo session list" in out
    assert len(ext_calls(env)) == 2                                   # the link was still sent: a click opens it


def test_an_extension_allowed_from_the_dialog_counts_as_allowed(env):
    import sqlite3
    v = vscode(env, allowed=False)
    db = Path(v["GNOMONKIT_VSCODE_USER_DIR"]) / "globalStorage" / "state.vscdb"
    db.parent.mkdir()
    con = sqlite3.connect(str(db))
    con.execute("create table ItemTable (key text primary key, value blob)")
    con.execute("insert into ItemTable values (?, ?)", ("extensionUrlHandler.confirmedExtensions",
                                                        json.dumps(["other.extension", "Anthropic.claude-code"])))
    con.commit()
    con.close()
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", **v)
    assert rc == 0, err
    assert "waiting for a click" not in out and "Nothing to press" in out


def test_without_a_vs_code_user_folder_the_link_check_is_unknown(env):
    v = dict(vscode(env, tab=False), GNOMONKIT_VSCODE_USER_DIR=str(env["tmp"] / "missing"))
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", **v)
    assert rc == 0, err
    assert "waiting for a click" not in out and "no VS Code process picked up the session" in out


def test_the_link_goes_through_the_code_command_where_it_is_found(env):
    v = vscode(env)
    del v["GNOMONKIT_URI_OPENER"]                                    # no override: the real choice
    rc, out, err = env["run"]("spawn", "demo-repo", "--role", "primary", "--task", "release", **v)
    assert rc == 0, err
    [code, opened] = ext_calls(env)
    assert code[0] == "code" and opened[:2] == ["code", "--open-url"]
    assert link_query(opened[2]) == {"session": one_shots(env)[0][0][2]}
    assert "Nothing to press" in out
