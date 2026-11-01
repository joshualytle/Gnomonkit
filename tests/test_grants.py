"""Orchestration grants: matching, default deny, the audit log, the CLI's terminal rule, `setup security`,
and the checks in session-dispatch and the interjector.

Synthetic data only: a made-up home, projects folder and sessions under pytest's tmp_path, and a fake
`claude` that records its arguments.
"""
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "plugins" / "core"
IJ = ROOT / "plugins" / "interjector"
DISPATCH = IJ / "skills" / "session-dispatch" / "dispatch.py"
for p in (str(CORE), str(CORE / "hooks"), str(IJ)):
    if p not in sys.path:
        sys.path.insert(0, p)
if str(IJ / "hooks") not in sys.path:
    sys.path.append(str(IJ / "hooks"))      # ij_store; after core's hooks, so gk_grants is core's copy

import gk_grants as G  # noqa: E402
import gnomonkit  # noqa: E402
from gnomonkit_cmds import _common as C  # noqa: E402
from gnomonkit_cmds import grants as GC  # noqa: E402

ME = "5e551011-aaaa-4bbb-8ccc-000000000001"          # the requesting session's (made-up) id
WORD = "grant-0042"
SCRUB = ("GNOMONKIT_", "CLAUDE_PLUGIN_OPTION_")
SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDE_CONFIG_DIR")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A made-up home and Gnomonkit folder; no Claude Code session unless a test sets one."""
    for k in list(os.environ):
        if k.startswith(SCRUB) or k in SESSION_VARS:
            monkeypatch.delenv(k, raising=False)
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    gk = h / ".gnomonkit"
    monkeypatch.setenv("GNOMONKIT_HOME", str(gk))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setattr(GC, "confirm_word", lambda kind="grant": WORD)
    return gk


@pytest.fixture
def projects(tmp_path):
    root = tmp_path / "projects"
    for name in ("alpha", "beta", "vault", os.path.join("alpha", "sub", "deep")):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def R(p) -> str:
    return G.resolve(p)


def write_grants(*grants, version=1, extra=""):
    lines = [] if version is None else ["version = %s" % json.dumps(version)]
    for g in grants:
        lines.append("\n[[grant]]")
        lines += ["%s = %s" % (k, json.dumps(v)) for k, v in g.items()]
    path = Path(G.grants_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")
    return path


def grant(frm, to, actions=("spawn",), agents=("claude",), **more):
    g = {"from": frm, "to": list(to), "actions": list(actions), "agents": list(agents)}
    g.update(more)
    return g


def audit_lines(area="dispatch"):
    p = Path(G.audit_path(area))
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# ---- the shared module ---------------------------------------------------------------------------
def test_core_and_interjector_ship_the_same_module():
    a = (CORE / "hooks" / "gk_grants.py").read_bytes()
    b = (IJ / "hooks" / "gk_grants.py").read_bytes()
    assert a == b, "plugins/interjector/hooks/gk_grants.py must be a byte-identical copy of core's"


# ---- matching ------------------------------------------------------------------------------------
def test_children_pattern_matches_folders_directly_under_it(projects):
    kids = R(projects) + "/*"
    write_grants(grant(kids, [kids]))
    assert G.decide(projects / "alpha", projects / "beta", "spawn").allowed
    assert not G.decide(projects, projects / "beta", "spawn").allowed                 # not the folder itself
    assert not G.decide(projects / "alpha" / "sub", projects / "beta", "spawn").allowed  # not two levels down
    assert not G.decide(projects / "alpha", projects.parent, "spawn").allowed


def test_descendants_pattern_matches_any_depth(projects):
    write_grants(grant(R(projects) + "/**", [R(projects) + "/**"]))
    assert G.decide(projects / "alpha" / "sub" / "deep", projects / "beta", "spawn").allowed
    assert G.decide(projects / "alpha", projects / "alpha" / "sub", "spawn").allowed
    assert not G.decide(projects, projects / "beta", "spawn").allowed


def test_exact_folder_and_anywhere(projects, tmp_path):
    write_grants(grant(R(projects / "alpha"), ["*"], actions=["message"]))
    assert G.decide(projects / "alpha", tmp_path / "anything", "message").allowed
    assert not G.decide(projects / "beta", projects / "alpha", "message").allowed
    assert not G.decide(projects / "alpha" / "sub", projects / "beta", "message").allowed


def test_same_project_and_except(projects):
    kids = R(projects) + "/*"
    write_grants(grant(kids, ["same-project"], actions=["handoff"]),
                 grant(kids, [kids], actions=["spawn"], **{"except": [R(projects / "vault")]}))
    assert G.decide(projects / "alpha", projects / "alpha", "handoff").allowed
    assert not G.decide(projects / "alpha", projects / "beta", "handoff").allowed
    assert G.decide(projects / "alpha", projects / "beta", "spawn").allowed
    assert not G.decide(projects / "alpha", projects / "vault", "spawn").allowed


def test_action_and_agent_must_both_match(projects):
    kids = R(projects) + "/*"
    write_grants(grant(kids, [kids], actions=["message"], agents=["claude", "codex"]),
                 grant(kids, [kids], actions=["stop"], agents=["gemini"]))
    loaded = G.load()
    assert loaded.ok, loaded.problems                    # other agents' names are accepted...
    assert G.decide(projects / "alpha", projects / "beta", "message").allowed
    assert not G.decide(projects / "alpha", projects / "beta", "spawn").allowed
    assert not G.decide(projects / "alpha", projects / "beta", "stop").allowed       # ...and ignored
    assert not G.decide(projects / "alpha", projects / "beta", "message", agent="codex").allowed


def test_paths_are_normalised(projects):
    dotted = str(projects / "beta" / ".." / "alpha").replace("\\", "/")
    write_grants(grant(dotted, [str(projects / "beta").replace("/", "\\") if os.name == "nt" else str(projects / "beta")]))
    assert G.decide(projects / "vault" / ".." / "alpha", projects / "beta", "spawn").allowed
    if os.name == "nt":
        assert G.decide(str(projects / "ALPHA").upper(), str(projects / "Beta"), "spawn").allowed


def test_symlinks_are_resolved(projects, tmp_path):
    link = tmp_path / "shortcut"
    try:
        os.symlink(projects / "beta", link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this machine can't make symlinks")
    write_grants(grant(R(projects / "alpha"), [str(link)]))
    assert G.decide(projects / "alpha", projects / "beta", "spawn").allowed
    write_grants(grant(R(projects / "alpha"), [R(projects / "beta")]))
    assert G.decide(projects / "alpha", link, "spawn").allowed


# ---- default deny --------------------------------------------------------------------------------
def test_no_file_refuses_and_says_how_to_fix(projects):
    d = G.decide(projects / "alpha", projects / "beta", "spawn")
    assert not d.allowed and d.status == "missing"
    text = G.refusal_text(d)
    assert "refused by orchestration grants" in text and "[[grant]]" in text
    assert 'from = "%s"' % R(projects / "alpha") in text and 'actions = ["spawn"]' in text
    # The full form, which runs in any shell: python "<core>/gnomonkit.py" ...
    assert 'python "%s" grants add --from "%s" --to "%s" --actions spawn' % (
        ROOT / "plugins" / "core" / "gnomonkit.py", R(projects / "alpha"), R(projects / "beta")) in text
    assert "setup security" in text


BAD = {
    "not TOML": "version = 1\n[[grant]\n",
    "no version": None,
    "unknown version": 2,
    "version as text": "1",
    "unknown action": grant("/a/*", ["/a/*"], actions=["spawn", "fly"]),
    "relative path": grant("projects/*", ["/a/*"]),
    "anywhere in from": grant("*", ["/a/*"]),
    "same-project in from": grant("same-project", ["/a/*"]),
    "wildcard mid-path": grant("/a/*/b", ["/a/*"]),
    "unknown key": grant("/a/*", ["/a/*"], when="always"),
    "empty actions": grant("/a/*", ["/a/*"], actions=[]),
    "bad agent name": grant("/a/*", ["/a/*"], agents=["Claude Code"]),
    "to as text": {"from": "/a/*", "to": "/a/*", "actions": ["spawn"], "agents": ["claude"]},
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_a_file_with_a_problem_refuses_everything(case, projects):
    kids = R(projects) + "/*"
    good = grant(kids, [kids], actions=list(G.ACTIONS))
    v = BAD[case]
    if case == "not TOML":
        Path(G.grants_path()).parent.mkdir(parents=True)
        Path(G.grants_path()).write_text(v, encoding="utf-8")
    elif case == "no version":
        write_grants(good, version=None)
    elif isinstance(v, (int, str)):
        write_grants(good, version=v)
    else:
        write_grants(good, v)                    # one good grant does not survive a bad one beside it
    loaded = G.load()
    assert not loaded.ok and loaded.problems
    d = G.decide(projects / "alpha", projects / "beta", "spawn", loaded=loaded)
    assert not d.allowed
    assert "grants list" in G.refusal_text(d)


def test_unreadable_file_refuses(projects):
    Path(G.grants_path()).mkdir(parents=True)             # a folder where the file should be
    loaded = G.load()
    assert loaded.status == "unreadable"
    assert not G.decide(projects / "alpha", projects / "beta", "spawn", loaded=loaded).allowed


def test_trust_tables_are_left_for_folder_trust(projects):
    kids = R(projects) + "/*"
    write_grants(grant(kids, [kids]), extra='\n[[trust]]\nroot = "%s"\nlevel = "work"\n' % R(projects))
    loaded = G.load()
    assert loaded.ok and loaded.data["trust"][0]["level"] == "work"


def test_fallback_reader_matches_tomllib_on_what_the_cli_writes(projects):
    tomllib = pytest.importorskip("tomllib")
    data = {"version": 1, "grant": [GC.build_grant(R(projects) + "/*", ["same-project", R(projects) + "/**"],
                                                   [R(projects / "vault")], ["all"])],
            "trust": [{"root": R(projects), "auto": True, "owners": []}]}
    text = GC.render(data)
    assert G._small_toml(text) == tomllib.loads(text)
    for bad in ('version = 1\nto = ["a" "b"]\n', "a = 1\na = 2\n", "v = 1.5\n", 'x = """a"""\n', "version =\n1\n"):
        with pytest.raises(ValueError):
            G._small_toml(bad)


# ---- who asks: the session's own folder, as a repository root ---------------------------------------
@pytest.fixture
def person(monkeypatch):
    """A person at a real terminal: what in_claude_code() needs, besides no CLAUDECODE, to skip the checks."""
    monkeypatch.setattr(G, "at_terminal", lambda stdin=None, stdout=None: True)


def test_outside_claude_code_nothing_is_checked_but_it_is_logged(projects, person):
    d = G.authorize("spawn", str(projects / "beta"))
    assert d.allowed and d.decision == "unchecked"
    [line] = audit_lines()
    assert line["decision"] == "unchecked" and line["action"] == "spawn" and line["event"] == "decision"


def test_inside_claude_code_from_is_the_sessions_own_folder(projects, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ME)
    monkeypatch.chdir(projects / "beta")                  # a `cd` in the agent's shell changes nothing
    write_grants(grant(R(projects / "alpha"), [R(projects / "vault")]))
    entries = [{"sessionId": ME, "id": "me01", "cwd": str(projects / "alpha"), "kind": "interactive"}]
    d = G.authorize("spawn", str(projects / "vault"), entries=entries, session="vault:job")
    assert d.allowed and d.from_dir == R(projects / "alpha") and d.grant == 1
    assert not G.authorize("spawn", str(projects / "vault"), entries=[]).allowed     # falls back to the cwd: beta
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    monkeypatch.setenv("CLAUDE_PID", "4242")
    entries[0]["pid"] = 4242
    assert G.authorize("spawn", str(projects / "vault"), entries=entries).allowed    # found by its process id
    lines = audit_lines()
    assert [x["decision"] for x in lines] == ["allow", "deny", "allow"]
    assert lines[0]["session"] == "vault:job" and lines[0]["requester"] == ME and lines[0]["grant"] == 1


def test_a_folder_inside_a_repository_counts_as_the_repository(projects, monkeypatch):
    if not _has_git():
        pytest.skip("git is not installed")
    subprocess.run(["git", "init", "-q", str(projects / "alpha")], check=True, capture_output=True)
    assert G.repo_root(projects / "alpha" / "sub" / "deep") == R(projects / "alpha")
    assert G.repo_root(projects / "beta") == R(projects / "beta")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.chdir(projects / "alpha" / "sub")
    write_grants(grant(R(projects / "alpha"), [R(projects / "alpha")], actions=["message"]))
    assert G.authorize("message", str(projects / "alpha" / "sub" / "deep"), log=False).allowed


def test_a_worktree_counts_as_its_project(projects, tmp_path):
    # The path forms, with or without git: <project>/.claude/worktrees/<name> and <project>.worktrees/<name>
    assert G.norm(G.worktree_home(R(projects / "alpha" / ".claude" / "worktrees" / "fix" / "src"))) == \
        G.norm(R(projects / "alpha"))
    assert G.norm(G.worktree_home(R(projects / "alpha.worktrees" / "fix"))) == G.norm(R(projects / "alpha"))
    assert G.norm(G.worktree_home(R(projects / "beta"))) == G.norm(R(projects / "beta"))
    if not _has_git():
        pytest.skip("git is not installed")
    repo = projects / "alpha"
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t" + "@" + "example.invalid"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(git + ["commit", "-q", "--allow-empty", "-m", "x"], check=True, capture_output=True)
    elsewhere = tmp_path / "checkouts" / "fix"                       # any `git worktree add` path
    subprocess.run(git + ["worktree", "add", "-q", str(elsewhere), "-b", "fix"], check=True, capture_output=True)
    assert G.norm(G.repo_root(elsewhere)) == G.norm(R(repo))
    write_grants(grant(R(repo), ["same-project"], actions=["handoff"]))
    assert G.decide(G.repo_root(elsewhere), G.repo_root(repo), "handoff").allowed


def test_the_spawn_grant_covers_handoff_and_its_own_subs_inside_one_project(projects, monkeypatch):
    """A project handing off to itself, and a session messaging or stopping the subs it started, need no
    grant beyond spawn; stopping any other session, or anything across projects, still does."""
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ME)
    alpha = str(projects / "alpha")
    write_grants(grant(R(projects / "alpha"), ["same-project"], actions=["spawn"]))
    mine = {"sessionId": "5e551011-aaaa-4bbb-8ccc-000000000002", "id": "sub01", "cwd": alpha, "kind": "background"}
    other = {"sessionId": "5e551011-aaaa-4bbb-8ccc-000000000003", "id": "oth01", "cwd": alpha, "kind": "background"}
    entries = [{"sessionId": ME, "id": "me01", "cwd": alpha, "kind": "interactive"}, mine, other]
    (Path(G.gnomonkit_home()) / "dispatch").mkdir(parents=True)
    (Path(G.gnomonkit_home()) / "dispatch" / "roles.json").write_text(json.dumps({
        "sub01": {"role": "sub", "project": alpha, "parent_id": ME, "sessionId": mine["sessionId"]},
        "oth01": {"role": "sub", "project": alpha, "parent_id": "someone-else", "sessionId": other["sessionId"]}}),
        encoding="utf-8")
    d = G.authorize("handoff", alpha, entries=entries, log=False)
    assert d.allowed and "spawn" in d.reason
    assert G.authorize("message", alpha, entries=entries, log=False, target=mine).allowed
    assert G.authorize("stop", alpha, entries=entries, log=False, target="sub01").allowed
    assert G.authorize("stop", alpha, entries=entries, log=False, target="me01").allowed      # itself
    assert not G.authorize("message", alpha, entries=entries, log=False, target="me01").allowed
    assert not G.authorize("stop", alpha, entries=entries, log=False, target=other).allowed   # not one it started
    assert not G.authorize("stop", alpha, entries=entries, log=False).allowed                 # no target named
    assert not G.authorize("handoff", str(projects / "beta"), entries=entries, log=False).allowed  # another project


def test_a_missing_grant_is_named_once_per_session(projects, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    write_grants(grant(R(projects / "alpha"), [R(projects / "vault")]))
    entries = [{"sessionId": ME, "id": "me01", "cwd": str(projects / "alpha"), "kind": "interactive"}]
    d = G.authorize("spawn", str(projects / "beta"), entries=entries, log=False)
    first = G.refusal_once(d, ME)
    assert "grants add" in first and "[[grant]]" in first
    again = G.refusal_once(d, ME)
    assert "grants add" not in again and "earlier in this session" in again
    assert "grants add" in G.refusal_once(d, "5e551011-aaaa-4bbb-8ccc-00000000000f")       # another session
    assert "grants add" in G.refusal_once(d)                                                 # no session id


def _has_git() -> bool:
    try:
        subprocess.run(["git", "--version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def test_unknown_target_folder_is_refused_inside_claude_code(projects, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    write_grants(grant(R(projects) + "/*", ["*"], actions=["stop"]))
    d = G.authorize("stop", None, entries=[], log=False)
    assert not d.allowed and d.status == "target"


# ---- the audit log -------------------------------------------------------------------------------
def test_audit_log_appends_one_json_line_per_event_privately(home):
    assert G.audit_append({"event": "decision", "note": "x" * 1000})
    assert G.audit_append({"event": "archive", "session": "s1"}, path=G.audit_path("trust"))
    assert G.audit_append({"event": "decision", "n": 2})
    lines = audit_lines()
    assert [x["event"] for x in lines] == ["decision", "decision"] and "time" in lines[0]
    assert len(lines[0]["note"]) < 500                                   # values are capped
    assert audit_lines("trust")[0]["event"] == "archive"
    if os.name != "nt":
        path = G.audit_path()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="this OS has no O_NOFOLLOW")
def test_audit_log_refuses_a_symlink(home, tmp_path):
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep\n", encoding="utf-8")
    os.makedirs(os.path.dirname(G.audit_path()))
    os.symlink(target, G.audit_path())
    assert G.audit_append({"event": "decision"}) is False
    assert target.read_text(encoding="utf-8") == "keep\n"


def test_without_claudecode_but_no_terminal_it_is_still_checked(projects, monkeypatch):
    """Clearing CLAUDECODE in an agent's shell doesn't give it a terminal, so the grants still apply."""
    monkeypatch.setattr(G, "at_terminal", lambda stdin=None, stdout=None: False)
    assert G.in_claude_code()
    assert G.authorize("spawn", str(projects / "beta")).decision == "deny"


def test_a_terminal_means_a_console_not_just_isatty(monkeypatch):
    """The Windows NUL device says isatty(); only a console has a console mode."""
    class Nul(io.StringIO):
        def isatty(self):
            return True

        def fileno(self):
            raise OSError("no handle")
    assert G.at_terminal(Nul(), Nul()) is (os.name != "nt")


def test_a_log_that_cannot_be_written_changes_no_decision(home, projects, person):
    os.makedirs(G.audit_path())                                           # a folder where the log should be
    assert G.audit_append({"event": "decision"}) is False
    assert G.authorize("spawn", str(projects / "beta")).decision == "unchecked"


# ---- the CLI: a person at a terminal -------------------------------------------------------------
class Term(io.StringIO):
    """A stream that says it is a terminal."""

    def isatty(self):
        return True


class Pipe(io.StringIO):
    def isatty(self):
        return False


def at_terminal(monkeypatch, answers=""):
    out = Term()
    monkeypatch.setattr(C, "_is_console", lambda stream: True)   # stands in for a console on Windows
    monkeypatch.setattr(sys, "stdin", Term(answers))
    monkeypatch.setattr(sys, "stdout", out)
    return out


def run_cli(*argv):
    return gnomonkit.main(list(argv))


def child_env(home):
    e = {k: v for k, v in os.environ.items() if not k.startswith(SCRUB) and k not in SESSION_VARS}
    e.update(HOME=os.environ["HOME"], USERPROFILE=os.environ["USERPROFILE"], GNOMONKIT_HOME=str(home))
    return e


@pytest.mark.parametrize("argv", [
    ["grants", "add", "--from", "/x/*", "--to", "same-project", "--actions", "spawn"],
    ["grants", "remove", "1"],
    ["setup", "security"],
])
def test_writing_commands_refuse_through_a_pipe(argv, home):
    """What an agent's shell looks like: stdin and stdout are pipes."""
    r = subprocess.run([sys.executable, str(CORE / "gnomonkit.py"), *argv], input="y\n%s\n" % WORD,
                       capture_output=True, text=True, encoding="utf-8", env=child_env(home),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 2, r.stderr
    assert "terminal" in r.stderr and "refused" in r.stderr
    assert not os.path.exists(G.grants_path())


def test_add_refuses_with_a_fake_non_terminal(home, monkeypatch, projects):
    monkeypatch.setattr(sys, "stdin", Pipe(WORD + "\n"))
    monkeypatch.setattr(sys, "stdout", Pipe())
    rc = run_cli("grants", "add", "--from", R(projects) + "/*", "--to", "same-project", "--actions", "spawn")
    assert rc == 2 and not os.path.exists(G.grants_path())
    monkeypatch.setattr(sys, "stdout", Term())                     # a terminal on one side only is not enough
    assert run_cli("grants", "remove", "1") == 2


def test_add_at_a_terminal_writes_a_checked_grant_and_keeps_the_rest(home, monkeypatch, projects):
    kids = R(projects) + "/*"
    old = write_grants(grant(kids, ["same-project"], actions=["handoff"]),
                       extra='\n[[trust]]\nroot = "%s"\nlevel = "work"\n' % R(projects))
    before = old.read_text(encoding="utf-8")
    out = at_terminal(monkeypatch, WORD + "\n")
    rc = run_cli("grants", "add", "--from", str(projects) + "/*", "--to", str(projects / "beta"),
                 "--to", "same-project", "--except", str(projects / "vault"), "--actions", "spawn,STOP")
    assert rc == 0, out.getvalue()
    assert "Type %s to confirm" % WORD in out.getvalue()
    loaded = G.load()
    assert loaded.ok and len(loaded.grants) == 2
    g = loaded.grants[1].source
    assert g == {"from": kids, "to": [R(projects / "beta"), "same-project"], "except": [R(projects / "vault")],
                 "actions": ["spawn", "stop"], "agents": ["claude"]}
    assert loaded.data["trust"] == [{"root": R(projects), "level": "work"}]
    assert Path(G.grants_path() + ".bak").read_text(encoding="utf-8") == before
    assert G.decide(projects / "alpha", projects / "beta", "stop").allowed
    [line] = audit_lines()
    assert line["event"] == "grants-add" and line["grant"]["from"] == kids
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(G.grants_path()).st_mode) == 0o600


def test_add_with_the_wrong_word_changes_nothing(home, monkeypatch, projects):
    at_terminal(monkeypatch, "yes\n")
    rc = run_cli("grants", "add", "--from", R(projects) + "/*", "--to", "same-project", "--actions", "all")
    assert rc == 2 and not os.path.exists(G.grants_path()) and audit_lines() == []


@pytest.mark.parametrize("bad", [
    ["--from", "relative/*", "--to", "same-project", "--actions", "spawn"],
    ["--from", "*", "--to", "same-project", "--actions", "spawn"],
    ["--from", "/x/*/y", "--to", "same-project", "--actions", "spawn"],
    ["--from", "/x/*", "--to", "same-project", "--actions", "fly"],
    ["--from", "/x/*", "--to", "same-project", "--actions", "spawn", "--agents", "Bad Name"],
])
def test_add_rejects_what_the_readers_would_refuse(bad, home, monkeypatch):
    at_terminal(monkeypatch, WORD + "\n")
    assert run_cli("grants", "add", *bad) == 2
    assert not os.path.exists(G.grants_path())


def test_add_never_overwrites_a_file_with_a_problem(home, monkeypatch, projects):
    path = write_grants(grant("relative", ["/a"]))
    before = path.read_text(encoding="utf-8")
    at_terminal(monkeypatch, WORD + "\n")
    assert run_cli("grants", "add", "--from", R(projects) + "/*", "--to", "same-project", "--actions", "spawn") == 2
    assert path.read_text(encoding="utf-8") == before


def test_remove_at_a_terminal(home, monkeypatch, projects):
    kids = R(projects) + "/*"
    write_grants(grant(kids, ["same-project"], actions=["handoff"]), grant(kids, [kids], actions=["message"]))
    at_terminal(monkeypatch, WORD + "\n")
    assert run_cli("grants", "remove", "1") == 0
    loaded = G.load()
    assert [g.actions for g in loaded.grants] == [["message"]]
    assert os.path.exists(G.grants_path() + ".bak")
    assert audit_lines()[-1]["event"] == "grants-remove"
    at_terminal(monkeypatch, WORD + "\n")
    assert run_cli("grants", "remove", "5") == 2


def test_list_and_check_only_read(home, capsys, projects):
    kids = R(projects) + "/*"
    write_grants(grant(kids, [kids], actions=["spawn", "message"], agents=["claude", "codex"]))
    assert run_cli("grants", "list") == 0
    out = capsys.readouterr().out
    assert "1 grant" in out and " 1. from " + kids in out and "codex not supported yet" in out
    assert run_cli("grants", "list", "--json") == 0
    assert json.loads(capsys.readouterr().out)["grants"][0]["number"] == 1
    assert run_cli("grants", "check", "--from", str(projects / "alpha"), "--to", str(projects / "beta"),
                   "--action", "message") == 0
    assert "allowed by grant 1" in capsys.readouterr().out
    assert run_cli("grants", "check", "--from", str(projects / "alpha"), "--to", str(projects / "beta"),
                   "--action", "stop") == 2
    assert "grants add" in capsys.readouterr().out
    assert audit_lines() == [] and not os.path.exists(G.grants_path() + ".bak")


def test_list_without_a_file(home, capsys):
    assert run_cli("grants", "list") == 0
    out = capsys.readouterr().out
    assert "not created yet" in out and "setup security" in out


def test_deny_rules_are_printed_and_no_settings_file_is_written(home, capsys, tmp_path, monkeypatch):
    assert run_cli("grants", "deny-rules") == 0
    out = capsys.readouterr().out
    assert '"Edit(~/.gnomonkit/grants.toml)"' in out and '"Write(~/.gnomonkit/grants.toml)"' in out
    assert '"Bash(claude --bg *)"' in out and '"PowerShell(claude stop *)"' in out
    assert "not a sandbox" in out or "None of this is a sandbox" in out
    assert not (Path(os.environ["HOME"]) / ".claude").exists()
    assert not (tmp_path / "claude-config").exists()
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "other-place"))
    rules = [r for r in GC.deny_rules() if r.startswith(("Edit(", "Write("))]
    assert len(rules) == 2
    assert all(r.split("(", 1)[1].startswith("//") for r in rules) and rules[0].endswith("other-place/grants.toml)")


# ---- setup security ------------------------------------------------------------------------------
def test_setup_security_asks_and_writes_grants(home, monkeypatch, projects):
    monkeypatch.setenv("GNOMONKIT_PROJECTS_ROOT", str(projects))
    # The grants' answers, then "n" and "" to the folder-trust section that follows (tests/test_folder_trust.py).
    out = at_terminal(monkeypatch, "\n" "n\n" "\n" "vault\n" + WORD + "\n" "n\n" "\n")
    assert run_cli("setup", "security") == 0, out.getvalue()
    text = out.getvalue()
    assert "<projects folder>" in text and "Edit(~/.gnomonkit/grants.toml)" in text
    loaded = G.load()
    assert loaded.ok
    [g] = loaded.grants
    assert g.source == {"from": R(projects) + "/*", "to": ["same-project"], "except": [R(projects / "vault")],
                        "actions": list(G.ACTIONS), "agents": ["claude"]}
    assert G.decide(projects / "alpha", projects / "alpha", "handoff").allowed
    assert not G.decide(projects / "alpha", projects / "beta", "spawn").allowed
    assert not G.decide(projects / "vault", projects / "vault", "message").allowed
    assert audit_lines()[-1]["event"] == "grants-setup"


def test_setup_security_across_projects_with_some_actions(home, monkeypatch, projects):
    out = at_terminal(monkeypatch, str(projects) + "\n" "y\n" "1, message\n" "\n" + WORD + "\n")
    assert GC.security_setup(None) == 0, out.getvalue()
    [g] = G.load().grants
    assert g.source["to"] == [R(projects) + "/*"] and g.actions == ["spawn", "message"] and not g.except_
    assert G.decide(projects / "alpha", projects / "beta", "spawn").allowed


def test_setup_security_keeps_existing_grants_unless_told(home, monkeypatch, projects):
    path = write_grants(grant(R(projects) + "/*", ["same-project"]))
    before = path.read_text(encoding="utf-8")
    at_terminal(monkeypatch, "n\n")
    assert GC.security_setup(None) == 0
    assert path.read_text(encoding="utf-8") == before


def test_setup_security_stops_cleanly_when_answers_run_out(home, monkeypatch, projects):
    at_terminal(monkeypatch, str(projects) + "\n")
    assert run_cli("setup", "security") == 2
    assert not os.path.exists(G.grants_path())


def test_setup_security_refuses_a_fake_non_terminal(home, monkeypatch):
    monkeypatch.setattr(sys, "stdin", Pipe("\n"))
    monkeypatch.setattr(sys, "stdout", Pipe())
    with pytest.raises(C.CommandError):
        GC.security_setup(None)


# ---- session-dispatch ----------------------------------------------------------------------------
FAKE = r'''import json, os, sys
state = os.environ["FAKE_CLAUDE_STATE"]
args = sys.argv[1:]
with open(os.path.join(state, "calls.jsonl"), "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"args": args, "cwd": os.getcwd()}) + "\n")
if args[:1] == ["agents"]:
    p = os.path.join(state, "agents.json")
    sys.stdout.write(open(p, encoding="utf-8").read() if os.path.exists(p) else "[]")
elif "--bg" in args:
    sys.stdout.write("bg42\n")
elif args[:1] == ["stop"]:
    sys.stdout.write("stopped\n")
else:
    sys.exit(3)
'''
BRIEF = "Synthetic brief: tidy the widget module and open a PR."


@pytest.fixture
def dispatch(tmp_path, home, projects):
    state = tmp_path / "state"
    state.mkdir()
    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE, encoding="utf-8")
    env = child_env(home)
    env.update(FAKE_CLAUDE_STATE=str(state), GNOMONKIT_CLAUDE_CMD=json.dumps([sys.executable, str(fake)]),
               GNOMONKIT_PROJECTS_ROOT=str(projects), CLAUDE_CONFIG_DIR=str(tmp_path / "claude-config"),
               CLAUDECODE="1", CLAUDE_CODE_SESSION_ID=ME,
               # No stop-when-idle watcher for a primary here (its grant check is in test_session_roles.py).
               GNOMONKIT_DISPATCH_PRIMARY_CLIENT="terminal")
    me = {"pid": 1, "cwd": str(projects / "alpha"), "kind": "interactive", "id": "me000001", "sessionId": ME,
          "name": "alpha:work", "status": "busy", "state": "working"}
    other = {"pid": 2, "cwd": str(projects / "beta"), "kind": "background", "id": "bg01", "sessionId": "0000-1",
             "name": "beta:job", "status": "busy", "state": "working"}
    (state / "agents.json").write_text(json.dumps([me, other]), encoding="utf-8")

    def run(*args, cwd=None, stdin=""):
        r = subprocess.run([sys.executable, str(DISPATCH), *args], capture_output=True, text=True, env=env,
                           input=stdin, encoding="utf-8", cwd=str(cwd or projects / "alpha"),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode, r.stdout, r.stderr

    def calls(kind):
        p = state / "calls.jsonl"
        rows = [json.loads(x)["args"] for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
        return [a for a in rows if (kind in a if kind == "--bg" else a[:1] == [kind])]

    return {"run": run, "calls": calls, "env": env}


def briefs(home):
    d = Path(home) / "dispatch"
    return sorted(d.glob("*.md")) if d.exists() else []


def test_dispatch_refuses_before_trust_or_brief(dispatch, home, projects):
    rc, out, err = dispatch["run"]("spawn", "beta", "--task", "tidy widget", "--prompt", BRIEF)
    assert rc == 2
    assert err.startswith("refused by orchestration grants") and 'gnomonkit.py" grants add' in err
    assert dispatch["calls"]("--bg") == [] and briefs(home) == []
    [line] = audit_lines()
    assert line["decision"] == "deny" and line["action"] == "spawn" and line["from"] == R(projects / "alpha")
    # A spawn is a sub of the requesting session (alpha:work), so it is titled after that session's task.
    assert line["to"] == R(projects / "beta") and line["session"] == "sub:work/tidy-widget"
    assert "widget module" not in Path(G.audit_path()).read_text(encoding="utf-8")     # never the brief


def test_dispatch_spawns_when_a_grant_allows_and_logs_it(dispatch, home, projects):
    kids = R(projects) + "/*"
    write_grants(grant(kids, [kids], actions=["spawn"]))
    rc, out, err = dispatch["run"]("spawn", "beta", "--task", "tidy widget", "--prompt", BRIEF)
    assert rc == 0, err
    assert len(dispatch["calls"]("--bg")) == 1 and len(briefs(home)) == 1
    [line] = audit_lines()
    assert (line["decision"], line["grant"]) == ("allow", 1)
    assert "widget module" not in Path(G.audit_path()).read_text(encoding="utf-8")


def test_dispatch_from_is_the_session_folder_not_the_shell_folder(dispatch, home, projects):
    write_grants(grant(R(projects / "beta"), [R(projects / "vault")]))
    rc, _, err = dispatch["run"]("spawn", "vault", "--task", "t", "--prompt", BRIEF, cwd=projects / "beta")
    assert rc == 2 and "a session in %s may not" % R(projects / "alpha") in err


def test_dry_run_reports_the_decision_and_logs_nothing(dispatch, home, projects):
    rc, _, err = dispatch["run"]("spawn", "beta", "--task", "t", "--prompt", BRIEF, "--dry-run")
    assert rc == 2 and err.startswith("dry run: refused by orchestration grants")
    write_grants(grant(R(projects) + "/*", [R(projects) + "/*"]))
    rc, out, err = dispatch["run"]("spawn", "beta", "--task", "t", "--prompt", BRIEF, "--dry-run")
    assert rc == 0, err
    assert "grant (spawn): allowed by grant 1" in out
    rc, out, _ = dispatch["run"]("spawn", "beta", "--task", "t", "--prompt", BRIEF, "--dry-run", "--json")
    assert json.loads(out)["grants"][0]["decision"] == "allow"
    assert audit_lines() == [] and briefs(home) == [] and dispatch["calls"]("--bg") == []


def test_handoff_that_retires_a_session_needs_the_stop_grant_too(dispatch, home, projects):
    kids = R(projects) + "/*"
    write_grants(grant(kids, [kids], actions=["handoff"]))
    rc, _, err = dispatch["run"]("handoff", "beta", "--task", "carry on", "--retire", "bg01", stdin="## Next\nShip.\n")
    assert rc == 2 and "may not stop the session in %s" % R(projects / "beta") in err
    assert dispatch["calls"]("--bg") == [] and dispatch["calls"]("stop") == [] and briefs(home) == []
    write_grants(grant(kids, [kids], actions=["handoff", "stop"]))
    rc, out, err = dispatch["run"]("handoff", "beta", "--task", "carry on", "--retire", "bg01",
                                   stdin="## Next\nShip.\n")
    assert rc == 0, err
    assert dispatch["calls"]("stop") == [["stop", "bg01"]] and "retired: bg01" in out
    assert [(x["action"], x["decision"]) for x in audit_lines()] == [
        ("handoff", "allow"), ("stop", "deny"), ("handoff", "allow"), ("stop", "allow")]


def test_stop_checks_the_grant_for_the_sessions_own_folder(dispatch, home, projects):
    rc, _, err = dispatch["run"]("stop", "bg01")
    assert rc == 2 and "may not stop the session" in err and dispatch["calls"]("stop") == []
    write_grants(grant(R(projects) + "/*", [R(projects / "beta")], actions=["stop"]))
    rc, out, err = dispatch["run"]("stop", "bg01", "--dry-run")
    assert rc == 0 and "grant (stop): allowed by grant 1" in out and dispatch["calls"]("stop") == []
    rc, out, err = dispatch["run"]("stop", "bg01")
    assert rc == 0, err
    assert dispatch["calls"]("stop") == [["stop", "bg01"]]
    rc, _, err = dispatch["run"]("stop", "not-listed")            # a session the list doesn't show
    assert rc == 2 and "unknown folder" in err


def test_dispatch_with_claudecode_cleared_is_still_checked(dispatch, home, projects):
    """A child process with pipes for stdin and stdout is no person at a terminal, CLAUDECODE or not."""
    for k in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID"):
        dispatch["env"].pop(k)
    rc, _, err = dispatch["run"]("spawn", "beta", "--task", "t", "--prompt", BRIEF)
    assert rc != 0 and "refused by orchestration grants" in err
    assert audit_lines()[0]["decision"] == "deny"


# ---- the interjector's say, block and hold -------------------------------------------------------
@pytest.fixture
def interjector(home, projects, monkeypatch):
    import importlib

    import ij_store
    importlib.reload(ij_store)
    import interjector.__main__ as cli
    importlib.reload(cli)
    from interjector import board
    ij_store.append_event("s1", {"event": "PreToolUse", "tool": "Bash", "target": "ls"})
    entries = [{"sessionId": ME, "cwd": str(projects / "alpha"), "kind": "interactive"},
               {"sessionId": "s1", "cwd": str(projects / "beta"), "kind": "background"}]
    monkeypatch.setattr(board, "fetch_agents", lambda: (entries, None))
    monkeypatch.chdir(projects / "alpha")
    return cli, ij_store, entries


def inbox(store, sid="s1"):
    return list((store.session_dir(sid) / "inbox").glob("*.json"))


def test_interjector_checks_message_only_inside_claude_code(interjector, home, projects, monkeypatch, capsys, person):
    cli, store, _ = interjector
    assert cli.main(["say", "--session", "s1", "from the user at a terminal"]) == 0
    assert audit_lines() == []                                    # the user needs no grant from themselves
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ME)
    assert cli.main(["hold", "--session", "s1", "keep going"]) == 2
    assert "may not message the session" in capsys.readouterr().err and len(inbox(store)) == 1
    write_grants(grant(R(projects) + "/*", [R(projects) + "/*"], actions=["message"]))
    assert cli.main(["block", "--session", "s1", "--match", "rm", "list first"]) == 0
    assert len(inbox(store)) == 2
    assert [x["decision"] for x in audit_lines()] == ["deny", "allow"]
    assert "list first" not in Path(G.audit_path()).read_text(encoding="utf-8")


def test_interjector_refuses_a_session_it_cannot_place(interjector, home, projects, monkeypatch, capsys):
    cli, store, entries = interjector
    write_grants(grant(R(projects) + "/*", ["*"], actions=["message"]))
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", ME)
    del entries[1]                                                # s1 is no longer in the session list
    assert cli.main(["say", "--session", "s1", "hi"]) == 2
    assert "unknown folder" in capsys.readouterr().err and inbox(store) == []
