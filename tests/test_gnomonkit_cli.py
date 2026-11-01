"""The gnomonkit command's shipped subcommands: setup, profile, install-launcher.

Synthetic data only: made-up profiles, a made-up plugin record and folders under pytest's tmp_path.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "plugins", "core")
sys.path.insert(0, CORE)

import gnomonkit  # noqa: E402
from gnomonkit_cmds import _common as C  # noqa: E402
from gnomonkit_cmds import launcher, profile, setup  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A fresh GNOMONKIT_HOME, and none of the developer's own options or GNOMONKIT_* settings."""
    for k in list(os.environ):
        if k.startswith("CLAUDE_PLUGIN_OPTION_") or k.startswith("GNOMONKIT_") or k == "CLAUDE_CONFIG_DIR":
            monkeypatch.delenv(k, raising=False)
    gk = tmp_path / "gk"
    monkeypatch.setenv("GNOMONKIT_HOME", str(gk))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    C._CACHE.clear()
    yield gk
    C._CACHE.clear()


def cli(capsys, *argv):
    rc = gnomonkit.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def personal(home):
    return home / "personal.toml"


# ---- profile set -----------------------------------------------------------------------------------
def test_set_writes_values_that_read_back_as_the_right_types(home, capsys):
    rc, out, _ = cli(capsys, "profile", "set", "communication.decisions=Prose", "context.context_budget=300k",
                     "safety.always_confirm=deleting data, publishing anything", "alerts.issues_only=yes",
                     "corrections.repo=example-org/agent-notes", "code.style=small functions, no globals")
    assert rc == 0
    assert "communication.decisions: (not set) -> prose" in out
    text = personal(home).read_text(encoding="utf-8")
    want = {"communication": {"decisions": "prose"}, "context": {"context_budget": 300000},
            "safety": {"always_confirm": ["deleting data", "publishing anything"]},
            "alerts": {"issues_only": True}, "corrections": {"repo": "example-org/agent-notes"},
            "code": {"style": "small functions, no globals"}}
    assert C._fallback_loads(text) == want
    if tomllib:
        assert tomllib.loads(text) == want
    assert C.validate_profile(C.load_toml(str(personal(home)))) == []


def test_one_bad_value_and_nothing_is_written(home, capsys):
    cli(capsys, "profile", "set", "communication.decisions=prose")
    before = personal(home).read_bytes()
    rc, out, err = cli(capsys, "profile", "set", "communication.reply_length=short",
                       "working.ask_before_acting=sometimes")
    assert rc == 2
    assert "nothing was written" in err and "always | risky | rarely" in err
    assert personal(home).read_bytes() == before
    assert not (home / "personal.toml.bak").exists()


def test_an_unknown_key_is_refused_with_the_nearest_known_one(home, capsys):
    rc, _, err = cli(capsys, "profile", "set", "communication.decision=prose")
    assert rc == 2
    assert "did you mean communication.decisions?" in err
    assert not personal(home).exists()
    rc, _, err = cli(capsys, "profile", "set", "noequals")
    assert rc == 2 and "KEY=VALUE" in err


def test_set_keeps_keys_it_does_not_know_and_backs_up_the_old_file(home, capsys):
    home.mkdir(parents=True)
    old = ('# my notes\n[communication]\ndecisions = "numbered"\nnickname = "x"\n\n'
           '[extra]\nflag = true\n\n[[extra.items]]\nname = "a"\n')
    personal(home).write_text(old, encoding="utf-8")
    rc, out, _ = cli(capsys, "profile", "set", "communication.reply_length=short")
    assert rc == 0
    assert (home / "personal.toml.bak").read_text(encoding="utf-8") == old
    data = C.load_toml(str(personal(home)))
    assert data["communication"] == {"decisions": "numbered", "reply_length": "short", "nickname": "x"}
    assert data["extra"] == {"flag": True, "items": [{"name": "a"}]}


def test_setting_the_same_value_again_changes_nothing(home, capsys):
    cli(capsys, "profile", "set", "memory.lessons=off")
    rc, out, _ = cli(capsys, "profile", "set", "memory.lessons=off")
    assert rc == 0 and "no change" in out
    assert not (home / "personal.toml.bak").exists()


def test_set_refuses_to_overwrite_a_profile_it_cannot_read(home, capsys):
    home.mkdir(parents=True)
    personal(home).write_text("[communication\nbroken = \n", encoding="utf-8")
    rc, _, err = cli(capsys, "profile", "set", "memory.lessons=off")
    assert rc == 2 and "can't read" in err
    assert personal(home).read_text(encoding="utf-8").startswith("[communication\n")


def test_scratch_root_is_stored_resolved_and_a_root_is_refused(home, tmp_path, capsys):
    rc, _, _ = cli(capsys, "profile", "set", "paths.scratch_root=%s" % (tmp_path / "a" / ".." / "scratch"))
    assert rc == 0
    assert C.load_toml(str(personal(home)))["paths"]["scratch_root"] == C.resolve_path(str(tmp_path / "scratch"))
    root = os.path.abspath(os.sep)
    rc, _, err = cli(capsys, "profile", "set", "paths.scratch_root=%s" % root)
    assert rc == 2 and "root" in err


def test_file_option_and_the_recorded_plugin_option_choose_the_file(home, tmp_path, capsys):
    other = tmp_path / "elsewhere" / "me.toml"
    cli(capsys, "profile", "set", "--file", str(other), "memory.lessons=off")
    assert C.load_toml(str(other)) == {"memory": {"lessons": "off"}}
    assert not personal(home).exists()
    # the session hook records the plugin option, which the agent's shell can't see
    recorded = tmp_path / "overlay" / "personal.toml"
    C.write_onboarding({"profile_option": str(recorded)})
    cli(capsys, "profile", "set", "delivery.prs=direct-ok")
    assert C.load_toml(str(recorded)) == {"delivery": {"prs": "direct-ok"}}


# ---- profile unset / show -------------------------------------------------------------------------
def test_unset_removes_the_key_and_says_what_applies_instead(home, capsys):
    cli(capsys, "profile", "set", "communication.decisions=prose", "memory.lessons=off")
    rc, out, _ = cli(capsys, "profile", "unset", "communication.decisions")
    assert rc == 0 and "default: numbered" in out
    assert C.load_toml(str(personal(home))) == {"memory": {"lessons": "off"}}
    rc, out, _ = cli(capsys, "profile", "unset", "communication.decisions")
    assert rc == 0 and "no change" in out
    rc, _, err = cli(capsys, "profile", "unset", "machine.shell")
    assert rc == 2 and "machine.toml" in err


def test_show_reports_where_each_value_comes_from(home, capsys, monkeypatch):
    cli(capsys, "profile", "set", "context.context_budget=300000", "communication.decisions=prose")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_MAX_COMPACTIONS", "5")
    rc, out, _ = cli(capsys, "profile", "show", "--json")
    assert rc == 0
    rows = {r["key"]: r for r in json.loads(out)["settings"]}
    assert (rows["context.context_budget"]["value"], rows["context.context_budget"]["source"]) == (300000, "profile")
    assert (rows["context.max_compactions"]["value"], rows["context.max_compactions"]["source"]) == ("5",
                                                                                                    "plugin option")
    assert (rows["working.ask_before_acting"]["value"], rows["working.ask_before_acting"]["source"]) == ("risky",
                                                                                                        "default")
    rc, out, _ = cli(capsys, "profile", "show")
    assert rc == 0 and "Default set" in out and "Advanced" in out and "prose" in out


def test_show_lists_problems_and_unknown_keys(home, capsys):
    home.mkdir(parents=True)
    personal(home).write_text('[communication]\ndecisions = "sometimes"\n[mine]\nx = 1\n', encoding="utf-8")
    rc, out, _ = cli(capsys, "profile", "show", "--json")
    data = json.loads(out)
    assert data["unknown_keys"] == ["mine"]
    assert any("communication.decisions" in p for p in data["problems"])


# ---- keys that live elsewhere ----------------------------------------------------------------------
def test_plan_goes_to_the_subscription_schedule_as_this_months_entry(home, capsys):
    rc, _, err = cli(capsys, "profile", "set", "plan.label=Example plan")
    assert rc == 2 and "plan.monthly_usd" in err
    rc, _, _ = cli(capsys, "profile", "set", "plan.label=Example plan", "plan.monthly_usd=$20")
    assert rc == 0
    month = time.strftime("%Y-%m")
    sched = json.loads((home / "subscription.json").read_text(encoding="utf-8"))
    assert sched == [{"from": month, "plan": "Example plan", "monthly_usd": 20}]
    cli(capsys, "profile", "set", "plan.monthly_usd=25.5")
    sched = json.loads((home / "subscription.json").read_text(encoding="utf-8"))
    assert sched == [{"from": month, "plan": "Example plan", "monthly_usd": 25.5}]
    assert not personal(home).exists()


def test_machine_shell_is_checked_against_the_shells_found_here(home, capsys, monkeypatch):
    sp = profile._session_profile()
    monkeypatch.setattr(sp, "detect_shells", lambda os_name: ["bash", "zsh"])
    monkeypatch.setattr(sp, "detect_gpu", lambda: "none")
    rc, _, err = cli(capsys, "profile", "set", "machine.shell=fish")
    assert rc == 2 and "bash | zsh" in err
    rc, out, _ = cli(capsys, "profile", "show", "--json")      # what setup reads to decide whether to ask
    assert {r["key"]: r["choices"] for r in json.loads(out)["settings"]}["machine.shell"] == "bash | zsh"
    rc, out, _ = cli(capsys, "profile", "set", "machine.shell=zsh")
    assert rc == 0
    assert C.load_toml(str(home / "machine.toml"))["shell"] == "zsh"
    assert not personal(home).exists()


def test_machine_kind_goes_to_the_machine_profile_and_keeps_the_shell(home, capsys, monkeypatch):
    sp = profile._session_profile()
    monkeypatch.setattr(sp, "detect_shells", lambda os_name: ["bash", "zsh"])
    monkeypatch.setattr(sp, "detect_gpu", lambda: "none")
    assert cli(capsys, "profile", "set", "machine.shell=zsh")[0] == 0
    rc, out, _ = cli(capsys, "profile", "set", "machine.kind=work", "communication.reply_length=short")
    assert rc == 0 and "machine.kind: work" in out
    saved = C.load_toml(str(home / "machine.toml"))
    assert saved["kind"] == "work" and saved["shell"] == "zsh"
    assert "kind" not in personal(home).read_text(encoding="utf-8")   # never in the synced profile
    rc, out, _ = cli(capsys, "profile", "show", "--json")
    row = {r["key"]: r for r in json.loads(out)["settings"]}["machine.kind"]
    assert row["value"] == "work" and row["choices"] == "personal | work | unknown"
    before = (home / "machine.toml").read_bytes()
    rc, _, err = cli(capsys, "profile", "set", "machine.kind=maybe")
    assert rc == 2 and "personal | work | unknown" in err
    assert (home / "machine.toml").read_bytes() == before


# ---- setup -----------------------------------------------------------------------------------------
def test_setup_done_and_skipped_are_recorded(home, capsys):
    rc, out, _ = cli(capsys, "setup")
    assert rc == 0 and "not done" in out and "not acknowledged" in out
    rc, out, _ = cli(capsys, "setup", "skipped")
    assert rc == 0 and C.read_onboarding()["state"] == "skipped"
    rc, out, _ = cli(capsys, "setup", "done")
    assert rc == 0 and C.read_onboarding()["state"] == "done"
    assert "setup security" in out
    cli(capsys, "profile", "set", "acknowledged.disclaimer=1")
    rc, out, _ = cli(capsys, "setup", "status")
    assert "First-run setup:   done" in out and "Disclaimer:        acknowledged" in out


def test_setup_keeps_other_onboarding_state(home, capsys):
    C.write_onboarding({"offers": 2, "profile_option": "x"})
    cli(capsys, "setup", "done")
    st = C.read_onboarding()
    assert (st["state"], st["offers"], st["profile_option"]) == ("done", 2, "x")


def test_setup_security_refuses_without_a_terminal(home):
    r = subprocess.run([sys.executable, os.path.join(CORE, "gnomonkit.py"), "setup", "security"],
                       capture_output=True, text=True, encoding="utf-8", stdin=subprocess.PIPE,
                       env=dict(os.environ))
    assert r.returncode == 2
    assert "refused" in r.stderr and "terminal" in r.stderr
    assert not (home / "personal.toml").exists()


def test_setup_security_says_what_is_coming_when_no_section_is_installed(home, capsys, monkeypatch):
    real = C.command_modules
    monkeypatch.setattr(C, "command_modules",
                        lambda: [m for m in real() if not callable(getattr(m[1], "security_setup", None))])
    monkeypatch.setattr(C, "is_terminal", lambda *a, **k: True)
    rc, out, _ = cli(capsys, "setup", "security")
    assert rc == 0 and "Nothing was changed" in out


def test_setup_security_runs_sections_in_order(home, capsys, monkeypatch):
    ran = []

    def section(name, order):
        return types.SimpleNamespace(SECURITY_ORDER=order, security_setup=lambda a: ran.append(name) or 0)

    fake = [("trust", section("trust", 20), None), ("grants", section("grants", 10), None),
            ("broken", None, "ImportError: x")]
    real = [m for m in C.command_modules() if not callable(getattr(m[1], "security_setup", None))]
    monkeypatch.setattr(C, "command_modules", lambda: real + fake)
    monkeypatch.setattr(C, "is_terminal", lambda *a, **k: True)
    rc, _, _ = cli(capsys, "setup", "security")
    assert rc == 0 and ran == ["grants", "trust"]


# ---- install-launcher ------------------------------------------------------------------------------
def _fake_install(tmp_path, version, key="core@gnomonkit", scope="user", extra=None):
    """A copy of core in a made-up plugin cache with its own version, and a record naming it."""
    dst = tmp_path / "cache" / key.split("@")[1] / "core" / version
    shutil.copytree(CORE, dst, ignore=shutil.ignore_patterns("__pycache__", "hooks", "skills"))
    pj = dst / ".claude-plugin" / "plugin.json"
    data = json.loads(pj.read_text(encoding="utf-8"))
    data["version"] = version
    pj.write_text(json.dumps(data), encoding="utf-8")
    plugins = {key: [{"scope": "project", "projectPath": str(tmp_path / "p"), "installPath": str(tmp_path / "no")},
                     {"scope": scope, "installPath": str(dst), "version": version}]}
    plugins.update(extra or {})
    cfg = tmp_path / "claude-config" / "plugins"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": plugins}), encoding="utf-8")
    return dst


def _launch(folder, *args):
    return subprocess.run([sys.executable, str(folder / "gnomonkit_launch.py"), *args], capture_output=True,
                          text=True, encoding="utf-8", env=dict(os.environ))


def test_launcher_runs_the_core_the_plugin_record_names(home, tmp_path, capsys):
    rc, out, _ = cli(capsys, "install-launcher")
    assert rc == 0 and "not on PATH" in out and "export PATH=" in out
    folder = home / "bin"
    assert launcher.installed(str(folder))
    _fake_install(tmp_path, "9.8.7")
    r = _launch(folder, "--version")
    assert r.returncode == 0 and "core 9.8.7" in r.stdout
    # after an update the record names the new folder, and the same launcher follows it
    _fake_install(tmp_path, "9.8.8")
    assert "core 9.8.8" in _launch(folder, "--version").stdout


def test_launcher_prefers_core_at_gnomonkit_and_ignores_other_scopes(home, tmp_path, capsys):
    cli(capsys, "install-launcher")
    other = _fake_install(tmp_path, "1.0.0", key="core@another-market")
    plugins = {"core@another-market": [{"scope": "user", "installPath": str(other)}]}
    _fake_install(tmp_path, "2.0.0", extra=plugins)
    assert "core 2.0.0" in _launch(home / "bin", "--version").stdout
    # a project-scope install only, so the record has no user entry: fall back to the installing core
    _fake_install(tmp_path, "3.0.0", scope="project")
    assert "core %s" % C.core_version() in _launch(home / "bin", "--version").stdout


def test_launcher_exit_codes_pass_through(home, tmp_path, capsys):
    cli(capsys, "install-launcher")
    _fake_install(tmp_path, "9.8.7")
    assert _launch(home / "bin", "setup", "bogus").returncode == 2


@pytest.mark.skipif(not shutil.which("sh"), reason="no sh here")
def test_the_sh_launcher_works(home, tmp_path, capsys):
    cli(capsys, "install-launcher")
    _fake_install(tmp_path, "9.8.7")
    r = subprocess.run(["sh", str(home / "bin" / "gnomonkit"), "--version"], capture_output=True, text=True,
                       env=dict(os.environ))
    assert r.returncode == 0 and "core 9.8.7" in r.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows only")
def test_the_cmd_launcher_works(home, tmp_path, capsys):
    cli(capsys, "install-launcher")
    _fake_install(tmp_path, "9.8.7")
    path = home / "bin" / "gnomonkit.cmd"
    assert b"\r\n" in path.read_bytes()
    r = subprocess.run(["cmd", "/c", str(path), "--version"], capture_output=True, text=True, env=dict(os.environ))
    assert r.returncode == 0 and "core 9.8.7" in r.stdout


def test_launcher_leaves_a_file_it_did_not_write(home, tmp_path, capsys):
    folder = tmp_path / "mybin"
    folder.mkdir()
    (folder / "gnomonkit").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    rc, _, err = cli(capsys, "install-launcher", "--dir", str(folder))
    assert rc == 2 and "didn't write" in err
    assert (folder / "gnomonkit").read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    assert not (folder / "gnomonkit_launch.py").exists()


def test_launcher_says_when_the_folder_is_already_on_path(home, tmp_path, capsys, monkeypatch):
    folder = tmp_path / "onpath"
    monkeypatch.setenv("PATH", str(folder) + os.pathsep + os.environ.get("PATH", ""))
    rc, out, _ = cli(capsys, "install-launcher", "--dir", str(folder))
    assert rc == 0 and "is on PATH" in out
    rc, out, _ = cli(capsys, "install-launcher", "--dir", str(folder))  # again: rewrites its own files
    assert rc == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_launcher_files_are_private(home, capsys):
    cli(capsys, "install-launcher")
    folder = home / "bin"
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(folder / "gnomonkit").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(folder / "gnomonkit_launch.py").st_mode) == 0o600


# ---- setup remote ----------------------------------------------------------------------------------
def _claude_settings(tmp_path, data=None):
    cfg = tmp_path / "claude-config"
    cfg.mkdir(exist_ok=True)
    p = cfg / "settings.json"
    if data is not None:
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return p


def test_setup_remote_on_merges_the_claude_setting_records_the_answer_and_prints_the_checklist(home, capsys,
                                                                                                tmp_path):
    p = _claude_settings(tmp_path, {"theme": "dark", "permissions": {"allow": ["Bash(git *)"]}})
    proj = tmp_path / "projects" / "alpha-shop"
    rc, out, _ = cli(capsys, "setup", "remote", "on", "--project", str(proj), "--project", str(tmp_path / "beta"))
    assert rc == 0, out
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"theme": "dark", "permissions": {"allow": ["Bash(git *)"]}, "remoteControlAtStartup": True}
    assert (tmp_path / "claude-config" / "settings.json.bak").read_text(encoding="utf-8").startswith("{")
    assert "remoteControlAtStartup = true" in out and "previous version" in out
    assert C.lookup(C.read_toml(str(personal(home))), "working.remote") == "yes"
    # One block per project, with the exact commands, and nothing run: the grants and trust files are untouched.
    assert out.count("in a terminal (an agent may not run these for you)") == 2
    assert 'grants add --from "%s" --to "%s" --actions spawn,handoff,stop' % (proj, proj) in out
    assert "accept the" in out and "trust sync" in out
    assert not (home / "grants.toml").exists() and not (home / "trust").exists()
    rc, out, _ = cli(capsys, "setup", "status")
    assert "Remote Control:    on (remoteControlAtStartup" in out


def test_setup_remote_off_and_status(home, capsys, tmp_path):
    p = _claude_settings(tmp_path, {"remoteControlAtStartup": True})
    rc, out, _ = cli(capsys, "setup", "remote", "off")
    assert rc == 0 and json.loads(p.read_text(encoding="utf-8")) == {"remoteControlAtStartup": False}
    assert C.lookup(C.read_toml(str(personal(home))), "working.remote") == "no"
    assert "Grants and folder trust are untouched" in out
    rc, out, _ = cli(capsys, "setup", "remote")
    assert rc == 0 and "Remote Control:    off; run" in out and "<project folder>" in out
    rc, out, _ = cli(capsys, "setup", "remote", "off")
    assert rc == 0 and "already off" in out and "saved" not in out


def test_setup_remote_creates_the_settings_file_when_there_is_none(home, capsys, tmp_path):
    rc, out, _ = cli(capsys, "setup", "remote", "on")
    assert rc == 0
    p = tmp_path / "claude-config" / "settings.json"
    assert json.loads(p.read_text(encoding="utf-8")) == {"remoteControlAtStartup": True}
    assert "previous version" not in out


def test_setup_remote_refuses_to_write_over_a_settings_file_it_cannot_read(home, capsys, tmp_path):
    p = _claude_settings(tmp_path)
    p.write_text("{not json", encoding="utf-8")
    rc, out, err = cli(capsys, "setup", "remote", "on")
    assert rc == 2 and "nothing was changed" in err
    assert p.read_text(encoding="utf-8") == "{not json" and not personal(home).exists()
    rc, out, _ = cli(capsys, "setup", "status")
    assert "Remote Control:    not set; run" in out


def test_profile_set_takes_working_remote_and_the_primary_client(home, capsys):
    rc, out, _ = cli(capsys, "profile", "set", "working.remote=yes", "dispatch.primary_client=vscode")
    assert rc == 0
    data = C.read_toml(str(personal(home)))
    assert data["working"]["remote"] == "yes" and data["dispatch"]["primary_client"] == "vscode"
    rc, out, err = cli(capsys, "profile", "set", "dispatch.primary_client=tab")
    assert rc == 2 and "any | terminal | vscode" in err


def test_setup_status_reports_the_launcher(home, capsys):
    _, out, _ = cli(capsys, "setup")
    assert "Launcher:          not installed" in out
    cli(capsys, "install-launcher")
    _, out, _ = cli(capsys, "setup")
    assert "Launcher:          installed" in out


@pytest.fixture
def bare_machine(tmp_path, monkeypatch):
    """PATH holds only an empty bin folder and the home folder is made up, so a real MemPalace install or
    Claude config on the developer's machine is never seen (CLAUDE_CONFIG_DIR is already a temp folder)."""
    b = tmp_path / "bin"
    b.mkdir()
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("PATH", str(b))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    return b


def _fake_mempalace(bin_dir):
    exe = bin_dir / ("mempalace.cmd" if os.name == "nt" else "mempalace")
    exe.write_text("@echo off\r\n" if os.name == "nt" else "#!/bin/sh\n", encoding="ascii")
    exe.chmod(0o755)


def _kind(home, kind):
    home.mkdir(exist_ok=True)
    (home / "machine.toml").write_text('kind = "%s"\n' % kind if kind else "", encoding="utf-8")
    C._CACHE.clear()


@pytest.mark.parametrize("kind", [None, "work", "unknown"])
def test_setup_status_never_mentions_mempalace_off_a_personal_machine(home, capsys, monkeypatch, bare_machine,
                                                                      kind):
    _kind(home, kind)
    _fake_mempalace(bare_machine)

    def no_look(*_a, **_k):
        raise AssertionError("looked for MemPalace on a machine that isn't personal")
    monkeypatch.setattr(C, "mempalace_status", no_look)
    rc, out, _ = cli(capsys, "setup")
    assert rc == 0
    assert "mempalace" not in out.lower()
    assert "Memory backend:    the built-in local history index" in out
    want = {None: "not set: the next session start asks", "work": "work",
            "unknown": "unknown (treated as a work machine)"}[kind]
    assert "Machine kind:      " + want in out


def test_setup_status_recommends_mempalace_on_a_personal_machine(home, capsys, bare_machine, tmp_path):
    _kind(home, "personal")
    _, out, _ = cli(capsys, "setup")
    assert "Machine kind:      personal" in out
    assert "Memory backend:    recommended on a personal machine: MemPalace (not set up here)." in out
    for part in (C.MEMPALACE_URL, "Licence: MIT.", "install and verify steps", "What can leave this machine",
                 "remote embedding endpoint", "Gnomonkit never installs it."):
        assert part in out

    _fake_mempalace(bare_machine)
    _, out, _ = cli(capsys, "setup")
    assert "MemPalace is installed (on PATH: " in out and "no MCP server for it yet" in out

    cfg = tmp_path / "claude-config"
    cfg.mkdir(exist_ok=True)
    (cfg / ".claude.json").write_text(json.dumps({"mcpServers": {"palace": {"command": "mempalace", "args": ["mcp"]}}}),
                                      encoding="utf-8")
    _, out, _ = cli(capsys, "setup")
    assert "Memory backend:    MemPalace, set up here (" in out and "mcpServers.palace" in out
    assert "What it is" not in out and "recommended" not in out


# ---- a script that falls back to the profile -------------------------------------------------------
def test_make_scratch_uses_the_profile_root_when_no_option_is_set(home, tmp_path, monkeypatch, capsys):
    import importlib.util
    path = os.path.join(CORE, "skills", "folder-placement", "make_scratch.py")
    spec = importlib.util.spec_from_file_location("make_scratch", path)
    ms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ms)
    assert ms.scratch_root() == os.path.join(str(home), "scratch")
    cli(capsys, "profile", "set", "paths.scratch_root=%s" % (tmp_path / "my-scratch"))
    C._CACHE.clear()
    assert ms.scratch_root() == C.resolve_path(str(tmp_path / "my-scratch"))
    monkeypatch.setenv("GNOMONKIT_SCRATCH_ROOT", str(tmp_path / "from-env"))
    assert ms.scratch_root() == str(tmp_path / "from-env")
