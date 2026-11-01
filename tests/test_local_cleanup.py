"""local_inventory.py and local_cleanup.py on a synthetic disk: real git repos in a temp folder."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "plugins" / "core" / "skills"
INV = SKILLS / "local-compare" / "local_inventory.py"
CLEAN = SKILLS / "local-cleanup" / "local_cleanup.py"


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def run(script, *args, env):
    r = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


@pytest.fixture
def disk(tmp_path):
    root = tmp_path / "projects"
    origin = tmp_path / "origin.git"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    main = root / "app"
    subprocess.run(["git", "clone", "-q", str(origin), str(main)], check=True, capture_output=True)
    for k, v in (("user.email", "t" + "@" + "example.invalid"), ("user.name", "t")):
        git(main, "config", k, v)
    (main / "a.txt").write_text("a")
    git(main, "add", "-A")
    git(main, "commit", "-qm", "init")
    git(main, "push", "-q", "origin", "main")
    git(main, "remote", "set-head", "origin", "main")
    # merged worktree: branch contained in main
    git(main, "worktree", "add", "-q", "-b", "feat/done", str(root / "app-done"), "origin/main")
    # rebased-then-merged: same patch on main under a different commit
    git(main, "worktree", "add", "-q", "-b", "feat/rebased", str(root / "app-rebased"), "origin/main")
    (root / "app-rebased" / "r.txt").write_text("r")
    git(root / "app-rebased", "add", "-A")
    git(root / "app-rebased", "commit", "-qm", "rebased change")
    git(root / "app-rebased", "push", "-q", "-u", "origin", "feat/rebased")
    (main / "m.txt").write_text("m")  # main moves on first, so the picked commit gets a new hash
    git(main, "add", "-A")
    git(main, "commit", "-qm", "unrelated")
    git(main, "cherry-pick", "feat/rebased")
    git(main, "push", "-q", "origin", "main")
    git(main, "fetch", "-q")
    # unmerged + dirty worktree: one local commit nobody pushed, one uncommitted file
    git(main, "worktree", "add", "-q", "-b", "feat/wip", str(root / "app-wip"), "origin/main")
    (root / "app-wip" / "c.txt").write_text("c")
    git(root / "app-wip", "add", "-A")
    git(root / "app-wip", "commit", "-qm", "local only")
    (root / "app-wip" / "w.txt").write_text("w")
    # a plain leftover folder, and one with an expired marker
    (root / "qa-data").mkdir()
    (root / "qa-data" / "x.bin").write_bytes(b"0" * 10)
    (root / "old-scratch").mkdir()
    (root / "old-scratch" / ".gnomonkit-made.json").write_text(json.dumps(
        {"by": "test", "purpose": "fixture", "expires": 1}))
    home = tmp_path / "home"
    cfg = tmp_path / "claude"
    (cfg / "plugins").mkdir(parents=True)
    (cfg / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "kit@mk": [{"scope": "user", "version": "1.0.0"},
                   {"scope": "local", "projectPath": str(tmp_path / "gone-project"), "version": "1.0.0"}]}}))
    env = dict(os.environ, GNOMONKIT_HOME=str(home), CLAUDE_CONFIG_DIR=str(cfg), HOME=str(tmp_path),
               USERPROFILE=str(tmp_path))
    return {"root": root, "main": main, "env": env, "tmp": tmp_path, "cfg": cfg}


def inventory(disk):
    out = disk["tmp"] / "inv.json"
    run(INV, "--root", str(disk["root"]), "--json", str(out), env=disk["env"])
    items = json.loads(out.read_text())["items"]
    return out, {(Path(i["path"]).name, i.get("plugin")): i for i in items}


def test_unpushed_counts_commits_on_no_remote_not_against_upstream(disk):
    """A branch tracking origin/main whose commits were pushed under another name has nothing to lose."""
    wt = disk["root"] / "app-tracks-main"
    git(disk["main"], "worktree", "add", "-q", "--track", "-b", "feat/tracks-main", str(wt), "origin/main")
    for n in ("1", "2"):
        (wt / f"t{n}.txt").write_text(n)
        git(wt, "add", "-A")
        git(wt, "commit", "-qm", f"t{n}")
    git(wt, "push", "-q", "origin", "HEAD:refs/heads/elsewhere")
    _, by = inventory(disk)
    assert by[("app-tracks-main", None)]["unpushed"] == 0
    (wt / "t3.txt").write_text("3")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "t3 only here")
    _, by = inventory(disk)
    assert by[("app-tracks-main", None)]["unpushed"] == 1


def test_verdicts(disk):
    _, by = inventory(disk)
    assert by[("app", None)]["verdict"] == "keep"
    assert by[("app-done", None)]["verdict"] == "safe"
    assert by[("app-rebased", None)]["merged"] == "equivalent"
    assert by[("app-rebased", None)]["verdict"] == "safe"
    assert by[("app-wip", None)]["verdict"] == "review"
    assert by[("qa-data", None)]["verdict"] == "review"
    assert by[("old-scratch", None)]["verdict"] == "safe"
    assert by[("gone-project", "kit@mk")]["verdict"] == "safe"


def test_dry_run_changes_nothing(disk):
    inv, _ = inventory(disk)
    out = run(CLEAN, "plan", "--inventory", str(inv), "--safe", env=disk["env"])
    assert "Dry run" in out and "would worktree remove" in out
    assert (disk["root"] / "app-done").is_dir() and (disk["root"] / "old-scratch").is_dir()


def test_apply_safe_then_restore(disk):
    inv, _ = inventory(disk)
    run(CLEAN, "plan", "--inventory", str(inv), "--safe", "--apply", env=disk["env"])
    assert not (disk["root"] / "app-done").exists()
    assert not (disk["root"] / "app-rebased").exists()
    assert (disk["root"] / "app-wip" / "w.txt").exists(), "a review item must not be touched by --safe"
    branches = subprocess.run(["git", "-C", str(disk["main"]), "branch"], capture_output=True, text=True).stdout
    assert "feat/done" not in branches and "feat/rebased" not in branches and "feat/wip" in branches
    data = json.loads((disk["cfg"] / "plugins" / "installed_plugins.json").read_text())
    assert data["plugins"]["kit@mk"] == [{"scope": "user", "version": "1.0.0"}]
    assert not (disk["root"] / "old-scratch").exists()
    listing = run(CLEAN, "list", env=disk["env"])
    batch = listing.split()[0]
    run(CLEAN, "restore", batch, env=disk["env"])
    assert (disk["root"] / "old-scratch" / ".gnomonkit-made.json").exists()


def test_keep_branches_removes_worktrees_but_no_branch(disk):
    inv, _ = inventory(disk)
    out = run(CLEAN, "plan", "--inventory", str(inv), "--safe", "--keep-branches", "--apply", env=disk["env"])
    assert not (disk["root"] / "app-done").exists() and "branch -D" not in out
    branches = subprocess.run(["git", "-C", str(disk["main"]), "branch"], capture_output=True, text=True).stdout
    assert "feat/done" in branches and "feat/rebased" in branches


def test_answers_remove_dirty_worktree_keeps_its_files_in_holding(disk):
    inv, by = inventory(disk)
    sys.path.insert(0, str(SKILLS / "local-compare"))
    import local_inventory as li
    ans = disk["tmp"] / "answers.txt"
    ans.write_text("FEEDBACK: Local cleanup\n- %s [Remove]\n- %s [Keep] mine\n"
                   % (li.section_id(by[("app-wip", None)]["path"]), li.section_id(by[("qa-data", None)]["path"])))
    run(CLEAN, "plan", "--inventory", str(inv), "--answers", str(ans), "--apply", env=disk["env"])
    assert not (disk["root"] / "app-wip").exists()
    held = list((Path(disk["env"]["GNOMONKIT_HOME"]) / "holding").rglob("w.txt"))
    assert held, "the uncommitted file must be in holding, not deleted"
    branches = subprocess.run(["git", "-C", str(disk["main"]), "branch"], capture_output=True, text=True).stdout
    assert "feat/wip" in branches, "an unmerged branch is never deleted"
    assert (disk["root"] / "qa-data").exists()
    _, again = inventory(disk)
    assert again[("qa-data", None)]["verdict"] == "keep"


def test_clone_is_refused(disk):
    inv, _ = inventory(disk)
    out = run(CLEAN, "plan", "--inventory", str(inv), "--path", str(disk["main"]), "--apply", env=disk["env"])
    assert "refuse" in out and (disk["main"] / "a.txt").exists()


def test_purge_only_after_cutoff(disk):
    inv, _ = inventory(disk)
    run(CLEAN, "plan", "--inventory", str(inv), "--safe", "--apply", env=disk["env"])
    out = run(CLEAN, "purge", "--older-than", "1", "--apply", env=disk["env"])
    assert "delete" not in out.replace("Dry run", "")
    assert list((Path(disk["env"]["GNOMONKIT_HOME"]) / "holding").iterdir())
