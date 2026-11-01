"""Interjector session board: `claude agents --json --all` plus git, rendered as one local page.

Synthetic data only: a fake `claude` that prints made-up sessions (in the shape the real CLI prints),
temp git repos with made-up names.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "interjector"))
sys.path.insert(0, str(ROOT / "plugins" / "interjector" / "hooks"))

FAKE = """import json, sys
assert sys.argv[2:] == ["agents", "--json", "--all"], sys.argv
sys.stdout.write(open(sys.argv[1], encoding="utf-8").read())
"""


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test" + "@" + "example.invalid", "-c",
                    "init.defaultBranch=main", *args], cwd=cwd, check=True, stdin=subprocess.DEVNULL,
                   capture_output=True)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "home"))
    for k in list(os.environ):                   # titles read with the default scheme, not the user's own
        if k.startswith("GNOMONKIT_NAMING_") or k in ("GNOMONKIT_PERSONAL_PROFILE",
                                                      "CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE"):
            monkeypatch.delenv(k, raising=False)
    import importlib
    import ij_store
    importlib.reload(ij_store)
    import ij_naming
    importlib.reload(ij_naming)
    from interjector import board
    importlib.reload(board)
    import interjector.__main__ as cli
    importlib.reload(cli)

    repo = tmp_path / "alpha-repo"
    (repo / "src").mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "init")
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    (repo / "new.txt").write_text("untracked\n", encoding="utf-8")
    wt = tmp_path / "alpha-wt"
    git(repo, "worktree", "add", "-q", "-b", "feat-y", str(wt))
    plain = tmp_path / "plain"
    plain.mkdir()

    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE, encoding="utf-8")
    data = tmp_path / "agents.json"

    def set_agents(entries):
        data.write_text(json.dumps(entries), encoding="utf-8")

    monkeypatch.setenv("GNOMONKIT_CLAUDE_CMD", json.dumps([sys.executable, str(fake), str(data)]))
    set_agents([])
    # Every sample session has a prompt in its transcript, so none is an empty tab the board hides.
    cfg = tmp_path / "cfg"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    chats = cfg / "projects" / "-samples"
    chats.mkdir(parents=True)
    for pid in range(11, 20):
        (chats / f"0000000{pid}-aaaa-bbbb-cccc-000000000000.jsonl").write_text(
            json.dumps({"type": "user", "message": {"content": "a synthetic prompt"}}) + "\n", encoding="utf-8")
    return {"tmp": tmp_path, "repo": repo, "wt": wt, "plain": plain, "set": set_agents, "board": board,
            "cli": cli, "store": ij_store}


def entry(cwd, status, name, pid, **kw):
    now_ms = int(time.time() * 1000)
    e = {"pid": pid, "cwd": str(cwd), "kind": "interactive", "startedAt": now_ms - pid * 1000,
         "sessionId": f"0000000{pid}-aaaa-bbbb-cccc-000000000000", "name": name, "status": status}
    e.update(kw)
    return e


def sample(env):
    return [
        entry(env["repo"], "idle", "alpha-repo:tidy-readme", 11),
        entry(env["wt"], "busy", "alpha-repo@feat-y:add-export", 12),
        entry(env["plain"], "waiting", "Release prep", 13, waitingFor="permission prompt"),
        entry(env["repo"] / "src", "waiting", "alpha-repo:fix-login", 14, waitingFor="input needed"),
        {"cwd": str(env["plain"]), "kind": "background", "startedAt": 1, "id": "bg01", "state": "done",
         "name": "nightly-check"},
    ]


def test_grouped_by_project_the_group_waiting_longest_first(env):
    env["set"](sample(env))
    b = env["board"].collect()
    assert b["error"] is None
    # alpha-repo (its folder, a subfolder and its linked worktree) waits longest, so it comes first;
    # inside each project, what needs you first.
    got = [(r["project"], r["state"]) for r in b["sessions"]]
    assert got == [("alpha-repo", "waiting"), ("alpha-repo", "busy"), ("alpha-repo", "idle"),
                   ("plain", "waiting"), ("plain", "ended")]
    assert [r["waitingFor"] for r in b["sessions"] if r["state"] == "waiting"] == ["input needed",
                                                                                   "permission prompt"]
    assert b["counts"] == {"waiting": 2, "busy": 1, "idle": 1, "ended": 1}


def test_git_enrichment_repo_branch_worktree_dirty(env):
    env["set"](sample(env))
    rows = {r["name"]: r for r in env["board"].collect()["sessions"]}
    main = rows["alpha-repo:tidy-readme"]
    assert (main["repo"], main["branch"], main["worktree"], main["dirty"]) == ("alpha-repo", "main", False, 2)
    sub = rows["alpha-repo:fix-login"]
    assert sub["repo"] == "alpha-repo" and sub["git"]
    wt = rows["alpha-repo@feat-y:add-export"]
    assert (wt["repo"], wt["branch"], wt["worktree"], wt["dirty"]) == ("alpha-repo@feat-y", "feat-y", True, 0)
    plain = rows["Release prep"]
    assert plain["git"] is False and plain["repo"] == "plain" and plain["dirty"] is None


def test_page_shows_titles_as_set_and_escapes_them(env, tmp_path):
    entries = sample(env)
    entries.append(entry(env["plain"], "idle", "<script>alert(1)</script>", 15))
    env["set"](entries)
    out = tmp_path / "board.html"
    assert env["board"].run(out=out) == 0
    page = out.read_text(encoding="utf-8")
    assert "2 sessions need you" in page
    assert "Release prep" in page                              # a user's own title shown as they set it
    assert "Waiting for: permission prompt" in page and "Waiting for: input needed" in page
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert "http-equiv" not in page                           # one-shot page does not reload itself
    assert page.index("fix-login") < page.index("add-export") < page.index("tidy-readme")


def test_missing_cli_fails_soft(env, tmp_path, monkeypatch):
    monkeypatch.setenv("GNOMONKIT_CLAUDE_CMD", json.dumps([str(tmp_path / "no-such-claude.exe")]))
    out = tmp_path / "b.html"
    assert env["board"].run(out=out) == 0
    page = out.read_text(encoding="utf-8")
    assert "Could not read sessions" in page and "No open sessions" in page


def test_garbage_from_cli_fails_soft(env, tmp_path):
    (tmp_path / "agents.json").write_text("this is not json", encoding="utf-8")
    b = env["board"].collect()
    assert b["sessions"] == [] and "not JSON" in b["error"]


def test_json_flag_prints_the_board(env, capsys):
    env["set"](sample(env))
    assert env["cli"].main(["board", "--json"]) == 0
    b = json.loads(capsys.readouterr().out)
    assert b["sessions"][0]["state"] == "waiting" and b["hidden"] == {"empty": 0, "archived": 0}
    assert {"repo", "branch", "worktree", "dirty", "waitingFor", "lastActivity"} <= set(b["sessions"][0])


def test_watch_rewrites_and_tracks_state_changes(env, tmp_path):
    entries = sample(env)[:1]
    env["set"](entries)
    since = {}
    b1 = env["board"].collect(since, now=1000.0)
    assert b1["sessions"][0]["stateSince"] == 1000.0 and b1["sessions"][0]["lastActivity"] is None
    entries[0]["status"], entries[0]["waitingFor"] = "waiting", "permission prompt"
    env["set"](entries)
    b2 = env["board"].collect(since, now=1060.0)
    r = b2["sessions"][0]
    assert r["state"] == "waiting" and r["stateSince"] == 1060.0 and r["lastActivity"] == 1060.0
    out = tmp_path / "w.html"
    assert env["board"].run(out=out, watch=0.01, max_polls=2) == 0
    assert 'http-equiv="refresh" content="1"' in out.read_text(encoding="utf-8")


def test_last_activity_from_the_interjector_event_log(env):
    e = sample(env)[0]
    env["set"]([e])
    env["store"].append_event(e["sessionId"], {"event": "PreToolUse", "tool": "Read", "target": "x"})
    r = env["board"].collect()["sessions"][0]
    assert r["lastActivity"] and abs(r["lastActivity"] - time.time()) < 60


def test_default_page_lives_in_the_interjector_folder(env):
    env["set"]([])
    assert env["cli"].main(["board"]) == 0
    assert (env["store"].home() / "board.html").is_file()


def test_watch_must_be_positive(env, capsys):
    assert env["cli"].main(["board", "--watch", "0"]) == 2
