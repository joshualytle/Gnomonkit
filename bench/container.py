"""Container mode: each run is a throwaway `docker run` with only its run dir, its plugins (read-only)
and a Claude config dir mounted. No capabilities, no privilege escalation, a pid limit, a memory limit.

The container IS the sandbox. Claude Code's own Bash sandbox (bubblewrap) cannot create namespaces
under --cap-drop ALL with Docker's default seccomp profile (verified 2026-10-23), and enabling it would
mean loosening that profile, which weakens the stronger boundary to make room for the weaker one.

Secrets never appear in the docker command line (which other processes can read): variables are
passed by name with `-e NAME`, and docker reads the value from the environment it is started with.
"""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parent / "container" / "Dockerfile"
# Passed through by name when present in the run env. Path-valued variables are rewritten below.
PASS_BY_NAME = ("CLAUDE_CODE_DISABLE_AUTO_MEMORY", "CLAUDE_CODE_SKIP_PROMPT_HISTORY",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "GNOMONKIT_MEMORY_GLOB", "GIT_CONFIG_NOSYSTEM",
                "ANTHROPIC_API_KEY", "ANTHROPIC_CUSTOM_HEADERS")
FIXED_ENV = {"CLAUDE_CONFIG_DIR": "/work/claude-config", "HOME": "/work/home", "GIT_CONFIG_GLOBAL": "/dev/null",
             "GNOMONKIT_HOME": "/work/run/state/gnomonkit", "GNOMONKIT_ROUTER_STATE_DIR": "/work/run/state/router"}


def _mount(src: Path, dst: str, readonly: bool = False) -> list[str]:
    spec = f"type=bind,src={Path(src)},dst={dst}" + (",readonly" if readonly else "")
    return ["--mount", spec]


def _stage_plugin(src: Path, run_dir: Path, i: int) -> Path:
    """Copy a plugin into the run dir and mount the copy. Docker Desktop shares host folders path by path,
    so a plugin mounted straight from a checkout or worktree that is not shared leaves the container stuck
    in "Created" waiting for a sharing approval nobody sees; the run dir sits under the bench root, which is."""
    dst = run_dir / "plugins" / str(i)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
    return dst


def container_launcher(image: str, runner=subprocess.run):
    def launch(cmd: list, cwd: Path, env: dict, timeout: int, stream_path: Path) -> int:
        repo = Path(cwd)
        run_dir = repo.parent
        name = f"gkbench-{uuid.uuid4().hex[:12]}"
        argv = ["docker", "run", "--rm", "--init", "--name", name,
                "--memory", "1500m", "--pids-limit", "512", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "-w", "/work/run/repo"]
        # Only what the run may change is writable. The stream and error files the run is scored on stay
        # outside every mount, so code inside the container cannot rewrite its own score.
        (run_dir / "state").mkdir(parents=True, exist_ok=True)
        argv += _mount(repo, "/work/run/repo")
        argv += _mount(run_dir / "state", "/work/run/state")
        if env.get("CLAUDE_CONFIG_DIR"):
            argv += _mount(Path(env["CLAUDE_CONFIG_DIR"]), "/work/claude-config")

        claude = list(cmd)
        plugin_i = 0
        for i, a in enumerate(cmd):
            if a == "--plugin-dir" and i + 1 < len(cmd):
                dst = f"/work/plugins/{plugin_i}"
                argv += _mount(_stage_plugin(Path(cmd[i + 1]), run_dir, plugin_i), dst, readonly=True)
                claude[i + 1] = dst
                plugin_i += 1
            elif a == "--mcp-config" and i + 1 < len(cmd):
                argv += _mount(Path(cmd[i + 1]), "/work/run/mcp.json", readonly=True)
                claude[i + 1] = "/work/run/mcp.json"

        child_env = {"PATH": env.get("PATH", "")}
        for k in PASS_BY_NAME:
            if k in env:
                argv += ["-e", k]
                child_env[k] = env[k]
        for k, v in FIXED_ENV.items():
            argv += ["-e", f"{k}={v}"]
        if env.get("GNOMONKIT_HISTORY_SEARCH"):
            # The planted history search lives in the run's state dir, which is mounted at /work/run/state.
            argv += ["-e", 'GNOMONKIT_HISTORY_SEARCH=["python3", "/work/run/state/history_search.py"]']
        argv += [image] + claude

        err_path = Path(stream_path).with_suffix(".err")
        with open(stream_path, "w", encoding="utf-8") as out, open(err_path, "w", encoding="utf-8") as err:
            try:
                return runner(argv, stdout=out, stderr=err, env=child_env, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                runner(["docker", "kill", name], capture_output=True)
                return -9
    return launch


def build_image(tag: str, runner=subprocess.run) -> str:
    runner(["docker", "build", "-t", tag, str(DOCKERFILE.parent)], check=True)
    return tag
