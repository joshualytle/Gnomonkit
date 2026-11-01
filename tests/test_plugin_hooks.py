"""Run every plugin's standalone hook test script (plugins/*/hooks/test_*.py) and require a pass.

The hook tests are self-contained scripts that exit non-zero on failure, so they also run on their
own (`python plugins/core/hooks/test_heredoc_guard.py`). This wrapper puts them in the one pytest run.
"""
import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "hooks", "test_*.py")))


@pytest.mark.parametrize("script", SCRIPTS, ids=[os.path.relpath(s, ROOT) for s in SCRIPTS])
def test_hook_script_passes(script):
    r = subprocess.run([sys.executable, script], capture_output=True, text=True, cwd=os.path.dirname(script))
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]


def test_every_marketplace_plugin_has_a_manifest():
    mk = json.load(open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8"))
    for p in mk["plugins"]:
        src = p["source"]
        assert isinstance(src, str) and src.startswith("./"), p["name"]
        manifest = os.path.join(ROOT, src, ".claude-plugin", "plugin.json")
        assert os.path.exists(manifest), "missing " + manifest
        assert json.load(open(manifest, encoding="utf-8"))["name"] == p["name"]


def test_hooks_json_points_at_existing_scripts():
    for hj in glob.glob(os.path.join(ROOT, "plugins", "*", "hooks", "hooks.json")):
        plugin_root = os.path.dirname(os.path.dirname(hj))
        data = json.load(open(hj, encoding="utf-8"))
        for entries in data["hooks"].values():
            for entry in entries:
                for h in entry["hooks"]:
                    cmd = h["command"]
                    path = cmd.split("${CLAUDE_PLUGIN_ROOT}/", 1)[1].split('"')[0]
                    assert os.path.exists(os.path.join(plugin_root, path)), cmd


# How every hook starts Python (plugins/core/README.md, "How the hooks start Python"). Claude Code runs
# the command through `sh -c` on macOS/Linux and Git Bash on Windows. python3 where it exists (many
# Linux/macOS systems have no `python`), but plain python under Git Bash, where python3 is often the
# Microsoft Store stub that is on PATH and doesn't run. Builtins only (no fork), and `exec` means one
# run whose exit code (2 = block) reaches Claude Code: never a `python x || python3 x` re-run.
HOOK_PY = ('case "$OSTYPE" in msys*|cygwin*) py=python;; *) command -v python3 >/dev/null 2>&1 '
           '&& py=python3 || py=python;; esac; exec "$py" ')


def _hook_commands():
    for hj in sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "hooks", "hooks.json"))):
        data = json.load(open(hj, encoding="utf-8"))
        for entries in data["hooks"].values():
            for entry in entries:
                for h in entry["hooks"]:
                    yield os.path.relpath(hj, ROOT), h


def test_every_hook_command_starts_python_portably():
    """A bare `python` exits 127 on python3-only systems and Claude Code carries on without the hook."""
    seen = 0
    for where, h in _hook_commands():
        seen += 1
        assert h.get("type") == "command", where
        assert "args" not in h and "shell" not in h, "%s: shell form, default shell: %r" % (where, h)
        cmd = h["command"]
        assert re.fullmatch(re.escape(HOOK_PY) + r'"\$\{CLAUDE_PLUGIN_ROOT\}/hooks/[A-Za-z0-9_]+\.py"', cmd), \
            "%s: hook command must be the portable form (HOOK_PY + the quoted script): %s" % (where, cmd)
    assert seen >= 10  # a floor against scanning nothing; core now runs a dispatcher per event


def _sh_path(p):
    """A directory as it goes in sh's colon-separated PATH (C:/x -> /c/x under Git Bash)."""
    s = p.as_posix()
    return "/" + s[0].lower() + s[2:] if sys.platform == "win32" and s[1:2] == ":" else s


def _sh_run(sh, cmd, env):
    if sys.platform == "win32":
        env = dict(env, SYSTEMROOT=os.environ.get("SYSTEMROOT", ""))
    return subprocess.run([sh, "-c", cmd], env=env, capture_output=True, text=True)


def test_portable_hook_form_picks_one_interpreter_and_keeps_exit_code(tmp_path):
    """Run HOOK_PY through the real sh with fake interpreters: the right one runs once, exit 2 survives."""
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("no sh on PATH")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "h.py").write_text("", encoding="utf-8")
    cmd = HOOK_PY + '"${CLAUDE_PLUGIN_ROOT}/hooks/h.py"'
    # bash sets OSTYPE itself (Git Bash: msys/cygwin, macOS: darwin); dash and friends read it from the
    # environment, so there every platform's branch can be exercised.
    probe = _sh_run(sh, 'printf %s "$OSTYPE"', {"PATH": "/usr/bin:/bin", "OSTYPE": "probe"}).stdout
    ostypes = ["linux-gnu", "msys", "cygwin"] if probe == "probe" else [probe]
    n = 0
    for ostype in ostypes:
        windows = ostype.startswith(("msys", "cygwin"))
        for have in (("python3",), ("python",), ("python", "python3")):
            want = "python" if windows or have == ("python",) else "python3"
            if windows and "python" not in have:
                continue  # Git Bash never tries python3: the Store stub would pass `command -v`
            n += 1
            bin_dir = tmp_path / ("bin%d" % n)
            bin_dir.mkdir()
            log = bin_dir / "ran.log"
            for name in have:
                exe = bin_dir / name
                exe.write_text('#!/bin/sh\necho %s >> "%s"\nexit 2\n' % (name, log.as_posix()),
                               encoding="utf-8", newline="\n")
                exe.chmod(0o755)
            # Only the fakes on PATH: a real python3 in /usr/bin (as on CI runners) would otherwise win.
            env = {"PATH": _sh_path(bin_dir), "CLAUDE_PLUGIN_ROOT": tmp_path.as_posix(),
                   "OSTYPE": ostype}
            r = _sh_run(sh, cmd, env)
            assert r.returncode == 2, (have, ostype, r.returncode, r.stderr)
            assert log.read_text().split() == [want], (have, ostype, log.read_text())
    assert n >= 2


def test_router_rules_point_at_shipped_skills():
    """A trigger for a skill that isn't in the plugin would suggest something that can't load."""
    for tj in glob.glob(os.path.join(ROOT, "plugins", "*", "hooks", "triggers.json")):
        plugin_root = os.path.dirname(os.path.dirname(tj))
        skills = set(os.listdir(os.path.join(plugin_root, "skills")))
        for rule in json.load(open(tj, encoding="utf-8"))["triggers"]:
            assert rule["skill"] in skills, rule["skill"]


def test_triggers_json_matches_yaml():
    """triggers.json is the runtime copy (no PyYAML needed); it must be regenerated from the YAML."""
    pytest.importorskip("yaml")
    builders = glob.glob(os.path.join(ROOT, "plugins", "*", "hooks", "build_triggers_json.py")) + \
        glob.glob(os.path.join(ROOT, "plugins", "*", "hooks", "build_skill_map_json.py"))
    for b in builders:
        r = subprocess.run([sys.executable, b, "--check"], capture_output=True, text=True, cwd=os.path.dirname(b))
        assert r.returncode == 0, r.stdout + r.stderr


def test_plugin_version_bumped_with_hook_or_skill_changes():
    """Installs only update when plugin.json's version changes. Guard against a hook or skill
    landing while the manifest still carries a version an earlier commit already shipped."""
    for manifest in glob.glob(os.path.join(ROOT, "plugins", "*", ".claude-plugin", "plugin.json")):
        plugin_dir = os.path.dirname(os.path.dirname(manifest))
        rel = os.path.relpath(plugin_dir, ROOT).replace("\\", "/")
        version = json.load(open(manifest, encoding="utf-8"))["version"]
        # The commit that last touched the plugin's hooks or skills...
        last_change = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", rel + "/hooks", rel + "/skills"],
            capture_output=True, text=True, cwd=ROOT).stdout.strip()
        if not last_change:
            continue
        # ...must not predate the commit that set the current version.
        set_version = subprocess.run(
            ["git", "log", "-1", "--format=%H", "-S", '"version": "%s"' % version, "--",
             rel + "/.claude-plugin/plugin.json"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
        if not set_version:
            continue  # version not committed yet (working tree); nothing to compare
        newer = subprocess.run(["git", "merge-base", "--is-ancestor", last_change, set_version],
                               cwd=ROOT).returncode == 0
        assert newer, "%s: hooks/skills changed after version %s was set; bump it" % (rel, version)


def _load(name):
    """Import plugins/core/hooks/<name>.py fresh, so module-level env reads see the test's env."""
    import importlib.util
    path = os.path.join(ROOT, "plugins", "core", "hooks", name + ".py")
    spec = importlib.util.spec_from_file_location("core_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_router_state_file_is_per_plugin():
    """An overlay ships its own copy of the router; both fire at once, so they must not share a file."""
    assert _load("skill_router")._plugin_name() == "core"


def test_router_state_dir_env_override(tmp_path, monkeypatch):
    """A benchmark run must keep router state inside its sandbox, not in the home directory."""
    monkeypatch.setenv("GNOMONKIT_ROUTER_STATE_DIR", str(tmp_path / "rs"))
    assert _load("skill_router").STATE_DIR == str(tmp_path / "rs")


@pytest.mark.parametrize("plugin", ["core", "supply-chain"])
def test_router_asks_for_the_load_and_never_approves_the_call(plugin, tmp_path):
    """A suggestion names plugin:skill and the Skill tool, and sends no permissionDecision: "allow"
    there would skip the user's permission prompt for every call the router matched."""
    hooks = os.path.join(ROOT, "plugins", plugin, "hooks")
    sys.path.insert(0, hooks)
    try:
        spec = importlib.util.spec_from_file_location(plugin + "_router_rules", os.path.join(hooks, "skill_router.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rule = next(r for r in mod.load_rules() if r.get("command_patterns") and (r.get("fixtures") or {}).get("fire"))
    finally:
        sys.path.remove(hooks)
    tool = "Bash" if "Bash" in (rule.get("tools") or ["Bash"]) else rule["tools"][0]
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "session_id": "routertest",
               "tool_input": {"command": rule["fixtures"]["fire"][0]},
               "cwd": (rule.get("repos") or ["anyrepo"])[0]}
    env = dict(os.environ, GNOMONKIT_ROUTER_STATE_DIR=str(tmp_path))
    out = subprocess.run([sys.executable, os.path.join(hooks, "skill_router.py")], input=json.dumps(payload),
                         capture_output=True, text=True, env=env).stdout
    hso = json.loads(out)["hookSpecificOutput"]
    assert "permissionDecision" not in hso
    assert "skill: %s:%s" % (plugin, rule["skill"]) in hso["additionalContext"]
    assert "Skill tool" in hso["additionalContext"]


def test_interlock_memory_glob_empty_reads_nothing(monkeypatch):
    """With the glob emptied, the interlock must not read the user's memory files at all."""
    monkeypatch.setenv("GNOMONKIT_MEMORY_GLOB", "")
    assert _load("destructive_interlock").search_memory("delete the exports directory with rm") == []


def test_every_skill_has_a_description():
    for sk in glob.glob(os.path.join(ROOT, "plugins", "*", "skills", "*", "SKILL.md")):
        head = open(sk, encoding="utf-8").read(2000)
        assert head.startswith("---") and "\ndescription:" in head, sk
