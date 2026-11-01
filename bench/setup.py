"""Sealed run setup: a fresh git repo per run, an allowlisted environment, and the claude command line.

Nothing here copies the caller's environment. A maintainer's shell can hold API tokens and backup
passphrases; a benchmark run, its hooks and any script the model writes must not see them.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from bench.arms import Arm
from bench.scenario import Scenario

WINDOWS_SYSTEM_VARS = ("SYSTEMROOT", "COMSPEC", "PATHEXT", "WINDIR")
# Not secrets, and Claude Code on Windows may need it to find Git Bash.
OPTIONAL_PASSTHROUGH = ("CLAUDE_CODE_GIT_BASH_PATH",)
API_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_CUSTOM_HEADERS")


def default_out_root() -> Path:
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"])
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "gnomonkit" / "bench"


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def check_out_dir(path: Path) -> None:
    """Refuse output inside a git work tree (it could be committed or mined into memory) or under a
    root the user listed in GNOMONKIT_BENCH_FORBID."""
    p = Path(path).resolve()
    for parent in (p, *p.parents):
        if (parent / ".git").exists():
            raise ValueError(f"output directory is inside a git work tree ({parent}); choose a path outside it")
    for root in filter(None, os.environ.get("GNOMONKIT_BENCH_FORBID", "").split(os.pathsep)):
        if _inside(p, Path(root)):
            raise ValueError(f"output directory is under a forbidden root ({root}, from GNOMONKIT_BENCH_FORBID)")


CREDENTIALS_FILE = ".credentials.json"


def round_env(round_dir: Path, billing: str, auth_dir: Path | None = None) -> dict[str, str]:
    """The COMPLETE child environment for one run. Built up from nothing, never copied.

    Every run gets its own config, home, temp and git config: a shared Claude config dir let one run
    plant a CLAUDE.md that every later run loaded. For subscription billing only the login file is
    copied in from auth_dir (where the user signs in once); nothing else from it travels."""
    round_dir = Path(round_dir)
    for sub in ("tmp", "home", "claude-config"):
        (round_dir / sub).mkdir(parents=True, exist_ok=True)
    if billing == "subscription" and auth_dir is not None and (Path(auth_dir) / CREDENTIALS_FILE).is_file():
        shutil.copyfile(Path(auth_dir) / CREDENTIALS_FILE, round_dir / "claude-config" / CREDENTIALS_FILE)
    gitconfig = round_dir / "gitconfig"
    gitconfig.touch()
    env = {"PATH": os.environ.get("PATH", "")}
    if sys.platform == "win32":
        env.update({k: os.environ[k] for k in WINDOWS_SYSTEM_VARS if k in os.environ})
    env.update({k: os.environ[k] for k in OPTIONAL_PASSTHROUGH if k in os.environ})
    tmp = str(round_dir / "tmp")
    env.update({
        "TEMP": tmp, "TMP": tmp, "TMPDIR": tmp,
        "HOME": str(round_dir / "home"), "USERPROFILE": str(round_dir / "home"),
        "CLAUDE_CONFIG_DIR": str(round_dir / "claude-config"),
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
    })
    if billing == "api":
        env.update({k: os.environ[k] for k in API_VARS if k in os.environ})
    return env


def _clear(path: Path) -> None:
    def make_writable_and_retry(func, p, *_):
        os.chmod(p, stat.S_IWRITE)      # git marks object files read-only on Windows
        func(p)
    if path.exists():
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=make_writable_and_retry)
        else:
            shutil.rmtree(path, onerror=make_writable_and_retry)


def setup_run(run_dir: Path, sc: Scenario, arm: Arm) -> tuple[Path, dict[str, str]]:
    """Fresh repo for one run. Returns (repo, per-run env additions)."""
    run_dir = Path(run_dir)
    _clear(run_dir)
    repo = run_dir / "repo"
    repo.mkdir(parents=True)
    for rel, body in sc.files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")
    lines = list(sc.claude_md) if arm.plant_claude_md else []
    if sc.history and sc.announce_history and arm.plant_claude_md:
        # Buried in the middle, as a real rule in a real CLAUDE.md would be, not shouted from the top.
        lines.insert(len(lines) // 2, "The user's past answers are searchable: run `python "
                     "../state/history_search.py \"<your question>\"` before asking them anything.")
    if lines:
        (repo / "CLAUDE.md").write_text("# acme-app\n\n" + "\n".join(f"- {line}" for line in lines) + "\n",
                                        encoding="utf-8", newline="\n")
    git_env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    if sys.platform == "win32":
        git_env.update({k: os.environ[k] for k in WINDOWS_SYSTEM_VARS if k in os.environ})
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=bench", "-c", "user.email=bench@example.com", "commit", "-qm", "init"]):
        subprocess.run(["git", *args], cwd=repo, env=git_env, check=True, capture_output=True)
    for rel, body in (sc.dirty or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")
    state = run_dir / "state"
    (state / "gnomonkit").mkdir(parents=True)
    (state / "router").mkdir()
    additions = {"GNOMONKIT_HOME": str(state / "gnomonkit"),
                 "GNOMONKIT_ROUTER_STATE_DIR": str(state / "router"),
                 "GNOMONKIT_MEMORY_GLOB": ""}
    if sc.history and arm.plant_claude_md:
        # Planted outside the repo, so the only way to it is the search, never reading a file.
        (state / "history.json").write_text(json.dumps(sc.history), encoding="utf-8")
        script = state / "history_search.py"
        script.write_text(HISTORY_SEARCH_SCRIPT, encoding="utf-8", newline="\n")
        additions["GNOMONKIT_HISTORY_SEARCH"] = json.dumps([sys.executable, str(script)])
    return repo, additions


# A synthetic history store: prints every past answer whose keywords appear in the query.
HISTORY_SEARCH_SCRIPT = """import json, os, sys
query = " ".join(sys.argv[1:]).lower()
here = os.path.dirname(os.path.abspath(__file__))
entries = json.load(open(os.path.join(here, "history.json"), encoding="utf-8"))
for topic, e in entries.items():
    if any(k.lower() in query for k in e.get("keys", [])):
        print("past answer (" + topic + "): " + e["text"])
"""


def empty_mcp_config(run_dir: Path) -> Path:
    p = Path(run_dir) / "mcp.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"mcpServers": {}}) + "\n", encoding="utf-8")
    return p


def build_cmd(sc: Scenario, arm: Arm, mcp: Path, model: str, max_turns: int) -> list[str]:
    cmd = ["claude", "-p", sc.prompt, "--setting-sources", "project"]
    for d in arm.plugin_dirs:
        cmd += ["--plugin-dir", str(d)]
    cmd += ["--strict-mcp-config", "--mcp-config", str(mcp), "--no-session-persistence",
            "--permission-mode", "acceptEdits", "--allowedTools", " ".join(sc.allowed_tools),
            "--model", model, "--max-turns", str(max_turns),
            "--output-format", "stream-json", "--verbose", "--include-hook-events"]
    return cmd
