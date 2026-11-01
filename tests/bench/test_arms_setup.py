"""Arms, sealed repo setup, the allowlisted child environment and the claude command line."""
import subprocess
from pathlib import Path

import pytest

from bench.arms import build_arms
from bench.scenario import Scenario
from bench.setup import build_cmd, check_out_dir, default_out_root, empty_mcp_config, round_env, setup_run

CORE = Path(__file__).resolve().parents[2] / "plugins" / "core"
B_SCENARIO = Scenario(name="b-test", family="precedent", polarity="positive",
                      files={"db/migrations/.gitkeep": "", "app.py": "def main():\n    return 0\n"},
                      prompt="Add a migration. Do the work directly, without asking questions.",
                      score=lambda repo, t: {"done": True, "applied": True},
                      claude_md=("Migrations are named <YYYYMMDD>_<HHMM>_<slug>.sql.",))


def test_written_arm_has_skills_but_no_hooks(tmp_path):
    arms = build_arms(CORE, tmp_path)
    w = arms["written"].plugin_dirs[0]
    assert (w / "skills" / "silent-no-op" / "SKILL.md").exists() and not (w / "hooks").exists()
    assert (w / ".claude-plugin" / "plugin.json").exists()
    assert arms["bare"].plugin_dirs == ()
    assert "custom" not in arms


def test_gnomonkit_arm_loads_every_gnomonkit_plugin(tmp_path):
    """Full Gnomonkit: core plus opt-in plugins such as the interjector."""
    dirs = build_arms(CORE, tmp_path)["gnomonkit"].plugin_dirs
    assert CORE in dirs and CORE.parent / "interjector" in dirs


def test_history_line_is_buried_in_a_long_claude_md(tmp_path):
    """Real CLAUDE.md files are long; the history instruction sits among other rules, not on top."""
    rules = tuple(f"Rule number {i} about something else." for i in range(40))
    sc = Scenario(name="h2", family="interjection", polarity="positive", files={"app.py": ""}, prompt="p",
                  score=B_SCENARIO.score, claude_md=rules,
                  history={"x": {"keys": ["x"], "text": "y"}})
    repo, _ = setup_run(tmp_path / "r", sc, build_arms(CORE, tmp_path)["written"])
    lines = [l for l in (repo / "CLAUDE.md").read_text(encoding="utf-8").splitlines() if l.startswith("- ")]
    pos = next(i for i, l in enumerate(lines) if "history_search.py" in l)
    assert len(lines) == 41 and 10 < pos < 30


def test_unannounced_history_is_searchable_but_never_mentioned(tmp_path):
    """The case the Interjector exists for: the history is there, but no rule tells Claude to look."""
    import json
    sc = Scenario(name="q", family="interjection", polarity="positive", files={"app.py": ""}, prompt="p",
                  score=B_SCENARIO.score, claude_md=("Some rule.",), announce_history=False,
                  history={"x": {"keys": ["x"], "text": "y"}})
    repo, env = setup_run(tmp_path / "r", sc, build_arms(CORE, tmp_path)["gnomonkit"])
    assert "history_search" not in (repo / "CLAUDE.md").read_text(encoding="utf-8")
    assert json.loads(env["GNOMONKIT_HISTORY_SEARCH"])


def test_history_is_planted_outside_the_repo_and_searchable(tmp_path):
    import json
    import subprocess
    import sys
    sc = Scenario(name="h", family="interjection", polarity="positive", files={"app.py": ""}, prompt="p",
                  score=B_SCENARIO.score,
                  history={"region": {"keys": ["region", "bucket"], "text": "new buckets go in eu-west-2"}})
    arms = build_arms(CORE, tmp_path)
    repo, env = setup_run(tmp_path / "r", sc, arms["gnomonkit"])
    assert not any("eu-west-2" in p.read_text(encoding="utf-8", errors="ignore") for p in repo.rglob("*") if p.is_file())
    argv = json.loads(env["GNOMONKIT_HISTORY_SEARCH"])
    out = subprocess.run(argv + ["which bucket region?"], capture_output=True, text=True).stdout
    assert "eu-west-2" in out
    assert subprocess.run(argv + ["unrelated"], capture_output=True, text=True).stdout.strip() == ""
    assert "history_search.py" in (repo / "CLAUDE.md").read_text(encoding="utf-8")
    repo_b, env_b = setup_run(tmp_path / "rb", sc, arms["bare"])
    assert "GNOMONKIT_HISTORY_SEARCH" not in env_b and not (repo_b / "CLAUDE.md").exists()


def test_custom_arm_present_only_when_named(tmp_path):
    arms = build_arms(CORE, tmp_path, custom=(tmp_path / "other-plugin",))
    assert arms["custom"].plugin_dirs == (tmp_path / "other-plugin",) and arms["custom"].plant_claude_md


def test_bare_gets_no_claude_md(tmp_path):
    repo, _ = setup_run(tmp_path / "r", B_SCENARIO, build_arms(CORE, tmp_path)["bare"])
    assert not (repo / "CLAUDE.md").exists()


def test_written_plants_claude_md_and_commits(tmp_path):
    repo, _ = setup_run(tmp_path / "r", B_SCENARIO, build_arms(CORE, tmp_path)["written"])
    assert "- Migrations are named" in (repo / "CLAUDE.md").read_text(encoding="utf-8")
    log = subprocess.run(["git", "-C", str(repo), "log", "--format=%ae"], capture_output=True, text=True)
    assert log.stdout.strip() == "bench@example.com"


def test_dirty_files_are_left_uncommitted(tmp_path):
    sc = Scenario(name="d", family="guards", polarity="positive", files={"notes.md": "v1\n"}, prompt="p",
                  score=B_SCENARIO.score, dirty={"notes.md": "v1\nmy unsaved thought\n", "new.txt": "x\n"})
    repo, _ = setup_run(tmp_path / "r", sc, build_arms(CORE, tmp_path)["bare"])
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert " M notes.md" in status and "?? new.txt" in status
    assert "unsaved" in (repo / "notes.md").read_text(encoding="utf-8")


def test_isolation_env_stays_inside_run_dir(tmp_path):
    _, env = setup_run(tmp_path / "r", B_SCENARIO, build_arms(CORE, tmp_path)["gnomonkit"])
    for k in ("GNOMONKIT_HOME", "GNOMONKIT_ROUTER_STATE_DIR"):
        assert Path(env[k]).resolve().is_relative_to((tmp_path / "r").resolve())
    assert env["GNOMONKIT_MEMORY_GLOB"] == ""


def test_rerun_over_readonly_git_objects(tmp_path):
    arm = build_arms(CORE, tmp_path)["bare"]
    setup_run(tmp_path / "r", B_SCENARIO, arm)
    setup_run(tmp_path / "r", B_SCENARIO, arm)       # must not raise on Windows


def test_round_env_is_an_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "synthetic-not-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-key")
    env = round_env(tmp_path, billing="subscription")
    assert "CLOUDFLARE_API_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1" and Path(env["CLAUDE_CONFIG_DIR"]).is_relative_to(tmp_path)
    assert env["CLAUDE_CODE_SKIP_PROMPT_HISTORY"] == "1" and env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert Path(env["GIT_CONFIG_GLOBAL"]).is_file()
    assert round_env(tmp_path, billing="api")["ANTHROPIC_API_KEY"] == "synthetic-key"


def test_out_dir_refused_inside_a_repo(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        check_out_dir(Path(__file__).resolve().parent / "out")          # inside the Gnomonkit work tree
    monkeypatch.setenv("GNOMONKIT_BENCH_FORBID", str(tmp_path / "projects"))
    with pytest.raises(ValueError):
        check_out_dir(tmp_path / "projects" / "bench-out")
    check_out_dir(tmp_path / "elsewhere")                               # fine


def test_default_out_root_is_a_user_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    root = default_out_root()
    assert root.parts[-2:] == ("gnomonkit", "bench") and root.is_relative_to(tmp_path)


def test_cmd_has_every_isolation_flag(tmp_path):
    mcp = empty_mcp_config(tmp_path)
    cmd = build_cmd(B_SCENARIO, build_arms(CORE, tmp_path)["gnomonkit"], mcp, "sonnet", 12)
    for flag in ("--setting-sources", "--strict-mcp-config", "--no-session-persistence", "--include-hook-events"):
        assert flag in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "project"
    assert cmd[cmd.index("--plugin-dir") + 1] == str(CORE)
    assert cmd[cmd.index("-p") + 1] == B_SCENARIO.prompt
    assert mcp.read_text(encoding="utf-8").strip() == '{"mcpServers": {}}'


def test_bare_cmd_loads_no_plugins(tmp_path):
    cmd = build_cmd(B_SCENARIO, build_arms(CORE, tmp_path)["bare"], empty_mcp_config(tmp_path), "sonnet", 12)
    assert "--plugin-dir" not in cmd
