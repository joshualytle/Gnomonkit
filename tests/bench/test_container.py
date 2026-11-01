"""Container mode: the docker argv is locked down, secrets never ride in argv, host paths never reach claude."""
import re
import subprocess
from pathlib import Path

from bench.container import DOCKERFILE, build_image, container_launcher


class FakeRunner:
    def __init__(self, timeout=False):
        self.calls, self.timeout = [], timeout

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.timeout and argv[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, 0, "", "")


def _launch(tmp_path, runner, env=None):
    run_dir = tmp_path / "round" / "a01-gnomonkit-0"
    repo = run_dir / "repo"
    repo.mkdir(parents=True)
    plug = tmp_path / "core"
    plug.mkdir(exist_ok=True)
    cfg = tmp_path / "shared" / "claude-config"
    cfg.mkdir(parents=True)
    base = {"CLAUDE_CONFIG_DIR": str(cfg), "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "GNOMONKIT_MEMORY_GLOB": "",
            "GNOMONKIT_HOME": str(run_dir / "state" / "gnomonkit"),
            "GNOMONKIT_ROUTER_STATE_DIR": str(run_dir / "state" / "router"),
            "PATH": "/host/path", "CLOUDFLARE_API_TOKEN": "synthetic-not-real", "ANTHROPIC_API_KEY": "synthetic-key"}
    cmd = ["claude", "-p", "Do it.", "--plugin-dir", str(plug), "--mcp-config", str(run_dir / "mcp.json"),
           "--model", "sonnet"]
    rc = container_launcher("gnomonkit-bench:test", runner=runner)(cmd, repo, {**base, **(env or {})}, 60,
                                                                   run_dir / "stream.jsonl")
    return rc, runner.calls, tmp_path


def test_run_argv_is_locked_down(tmp_path):
    rc, calls, _ = _launch(tmp_path, FakeRunner())
    argv = calls[0][0]
    assert rc == 0 and argv[:2] == ["docker", "run"]
    for flag in ("--rm", "--init", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit"):
        assert flag in argv
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    assert any("dst=/work/plugins/0" in m and m.endswith("readonly") for m in mounts)
    assert any("dst=/work/run" in m and "readonly" not in m for m in mounts)


def test_plugins_are_mounted_from_a_copy_in_the_run_dir(tmp_path):
    """Docker Desktop shares host folders one by one: a bind mount from a folder it does not share leaves
    the container stuck in "Created" until the run times out. The copy lives under the shared bench root."""
    run_dir = tmp_path / "round" / "a01-gnomonkit-0"
    (tmp_path / "core" / "hooks").mkdir(parents=True)
    (tmp_path / "core" / "hooks" / "h.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "core" / "__pycache__").mkdir()
    _, calls, _ = _launch(tmp_path, FakeRunner())
    argv = calls[0][0]
    m = next(argv[i + 1] for i, a in enumerate(argv) if a == "--mount" and "dst=/work/plugins/0" in argv[i + 1])
    src = Path(m.split("src=")[1].split(",dst=")[0])
    assert src == run_dir / "plugins" / "0"
    assert (src / "hooks" / "h.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (src / "__pycache__").exists()


def test_scored_files_are_not_mounted(tmp_path):
    """Review #8: with the whole run dir mounted, container code could rewrite the stream it is scored on."""
    _, calls, _ = _launch(tmp_path, FakeRunner())
    argv = calls[0][0]
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    dsts = {m.split("dst=")[1].split(",")[0]: m for m in mounts}
    assert "/work/run" not in dsts
    assert "readonly" not in dsts["/work/run/repo"] and "readonly" not in dsts["/work/run/state"]
    assert dsts["/work/run/mcp.json"].endswith("readonly")
    assert not any("stream" in m for m in mounts)


def test_secrets_never_in_argv_and_non_allowlisted_never_passed(tmp_path):
    _, calls, _ = _launch(tmp_path, FakeRunner())
    argv, kw = calls[0]
    joined = " ".join(argv)
    assert "synthetic-key" not in joined and "synthetic-not-real" not in joined
    assert "ANTHROPIC_API_KEY" in argv                       # passed by name; value comes from the runner env
    assert "CLOUDFLARE_API_TOKEN" not in joined
    assert kw["env"]["ANTHROPIC_API_KEY"] == "synthetic-key" and "CLOUDFLARE_API_TOKEN" not in kw["env"]


def test_claude_args_use_container_paths(tmp_path):
    _, calls, tmp = _launch(tmp_path, FakeRunner())
    argv = calls[0][0]
    claude = argv[argv.index("claude"):]
    assert str(tmp) not in " ".join(claude) and str(tmp).replace("\\", "/") not in " ".join(claude)
    assert claude[claude.index("--plugin-dir") + 1] == "/work/plugins/0"
    assert claude[claude.index("--mcp-config") + 1] == "/work/run/mcp.json"
    assert argv[argv.index("-w") + 1] == "/work/run/repo"


def test_no_in_container_sandbox_setting(tmp_path):
    """bubblewrap cannot create namespaces under --cap-drop ALL + default seccomp (verified 2026-10-23);
    loosening seccomp to allow it would weaken the container. The container is the boundary."""
    _, calls, _ = _launch(tmp_path, FakeRunner())
    assert "--settings" not in calls[0][0] and "seccomp=unconfined" not in " ".join(calls[0][0])


def test_history_search_is_rewritten_to_the_container_path(tmp_path):
    import json
    _, calls, _ = _launch(tmp_path, FakeRunner(),
                          env={"GNOMONKIT_HISTORY_SEARCH": json.dumps(["C:/py/python.exe", str(tmp_path / "x.py")])})
    argv = calls[0][0]
    vals = [argv[i + 1] for i, a in enumerate(argv) if a == "-e" and argv[i + 1].startswith("GNOMONKIT_HISTORY_SEARCH=")]
    assert vals and json.loads(vals[0].split("=", 1)[1]) == ["python3", "/work/run/state/history_search.py"]


def test_timeout_kills_the_container_by_name(tmp_path):
    rc, calls, _ = _launch(tmp_path, FakeRunner(timeout=True))
    name = calls[0][0][calls[0][0].index("--name") + 1]
    assert rc == -9 and calls[-1][0] == ["docker", "kill", name]


def test_dockerfile_is_pinned():
    text = Path(DOCKERFILE).read_text(encoding="utf-8")
    from_line = next(l for l in text.splitlines() if l.startswith("FROM"))
    assert "@sha256:" in from_line and "latest" not in from_line
    assert re.search(r"@anthropic-ai/claude-code@\d+\.\d+\.\d+", text) and "USER bench" in text


def test_build_image_uses_the_dockerfile(tmp_path):
    runner = FakeRunner()
    assert build_image("gnomonkit-bench:test", runner=runner) == "gnomonkit-bench:test"
    argv = runner.calls[0][0]
    assert argv[:3] == ["docker", "build", "-t"] and str(Path(DOCKERFILE).parent) in argv
