"""Tests for plugins/core/hooks/session_profile.py. Synthetic profiles in temp folders only."""
import builtins
import importlib.util
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "plugins", "core", "hooks", "session_profile.py")

PERSONAL = """
[communication]
decisions = "numbered"
reply_length = "short"

[delivery]
ci = "hosted"
hosted_minutes_budget = 3000
commit_email = "noreply"

[alerts]
destination = "slack"
issues_only = true

[safety]
always_confirm = ["production restarts", "anything deleting data"]
"""


def load_module():
    spec = importlib.util.spec_from_file_location("session_profile", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def clean_env(tmp_path):
    """The developer's own options and GNOMONKIT_* settings never reach the hook under test."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "GNOMONKIT_")) and k != "CLAUDE_CODE_REMOTE"}
    env["GNOMONKIT_HOME"] = str(tmp_path / "gk")
    return env


def run(tmp_path, *args, personal=None, remote=False, source=None, env_extra=None):
    env = clean_env(tmp_path)
    if personal is not None:
        p = tmp_path / "personal.toml"
        p.write_text(personal, encoding="utf-8")
        env["GNOMONKIT_PERSONAL_PROFILE"] = str(p)
    if remote:
        env["CLAUDE_CODE_REMOTE"] = "true"
    env.update(env_extra or {})
    event = {"hook_event_name": "SessionStart"}
    if source:
        event["source"] = source
    r = subprocess.run([sys.executable, HOOK, *args], input=json.dumps(event),
                       capture_output=True, text=True, env=env, encoding="utf-8")
    return r


def ctx(r):
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def test_first_run_asks_and_names_the_save_command(tmp_path):
    c = ctx(run(tmp_path))
    mod = load_module()
    assert "NO MACHINE PROFILE YET" in c
    assert "OS " + mod.detect_os() in c
    assert "--write-machine" in c
    assert "personal or a work machine" in c and "--kind <personal|work|unknown>" in c
    assert not (tmp_path / "gk" / "machine.toml").exists()   # the hook itself never writes


def test_write_machine_then_quiet(tmp_path):
    mod = load_module()
    shells = mod.detect_shells(mod.detect_os())
    if not shells:
        pytest.skip("no known shell on this test machine")
    r = run(tmp_path, "--write-machine", "--shell", shells[0], "--kind", "work")
    assert r.returncode == 0, r.stderr
    saved = (tmp_path / "gk" / "machine.toml").read_text(encoding="utf-8")
    assert 'shell = "%s"' % shells[0] in saved and 'kind = "work"' in saved
    c = ctx(run(tmp_path))
    assert "NO MACHINE PROFILE" not in c and "OUT OF DATE" not in c and "INCOMPLETE" not in c
    assert "Write shell commands for: " + shells[0] in c
    assert "Machine kind: work" in c


def test_write_machine_rejects_missing_shell(tmp_path):
    r = run(tmp_path, "--write-machine", "--shell", "not-a-real-shell")
    assert r.returncode == 2


def _machine_without_kind(tmp_path, mod):
    shells = mod.detect_shells(mod.detect_os())
    d = tmp_path / "gk"
    d.mkdir(exist_ok=True)
    text = 'os = "%s"\ngpu = "Example GPU (4 GB)"\n' % mod.detect_os()
    if shells:
        text += 'shell = "%s"\n' % shells[0]
    (d / "machine.toml").write_text(text, encoding="utf-8")
    return d / "machine.toml", shells


def test_a_machine_profile_without_a_kind_asks_for_it_once(tmp_path):
    """A second machine whose personal profile was synced from the first still gets asked."""
    mod = load_module()
    path, shells = _machine_without_kind(tmp_path, mod)
    c = ctx(run(tmp_path, personal=PERSONAL))
    assert "MACHINE PROFILE INCOMPLETE" in c and "personal or a work machine" in c
    assert "--write-machine --kind <personal|work|unknown>" in c
    assert "Machine kind:" not in c
    r = run(tmp_path, "--write-machine", "--kind", "personal")
    assert r.returncode == 0, r.stderr
    data = mod.parse_toml(path.read_text(encoding="utf-8"))
    assert data["kind"] == "personal" and data.get("shell") == (shells[0] if shells else None)
    c = ctx(run(tmp_path, personal=PERSONAL))
    assert "INCOMPLETE" not in c and "Machine kind: personal" in c
    assert "kind" not in (tmp_path / "personal.toml").read_text(encoding="utf-8")


def test_unknown_kind_is_not_asked_again_and_reads_as_work(tmp_path):
    mod = load_module()
    _machine_without_kind(tmp_path, mod)
    assert run(tmp_path, "--write-machine", "--kind", "unknown").returncode == 0
    c = ctx(run(tmp_path))
    assert "INCOMPLETE" not in c
    assert "Machine kind: unknown (treat it as a work machine)" in c


def test_write_machine_rejects_a_kind_outside_the_choices(tmp_path):
    r = run(tmp_path, "--write-machine", "--kind", "maybe")
    assert r.returncode == 2 and "personal, work, unknown" in r.stderr
    assert not (tmp_path / "gk" / "machine.toml").exists()


def test_no_machine_questions_where_onboarding_is_off(tmp_path):
    quiet = {"GNOMONKIT_NO_ONBOARDING": "1"}
    assert "NO MACHINE PROFILE YET" not in ctx(run(tmp_path, env_extra=quiet))
    _machine_without_kind(tmp_path, load_module())
    assert "INCOMPLETE" not in ctx(run(tmp_path, env_extra=quiet))


def test_drift_asks_before_updating(tmp_path):
    mod = load_module()
    other = "linux" if mod.detect_os() != "linux" else "windows"
    d = tmp_path / "gk"
    d.mkdir()
    (d / "machine.toml").write_text('os = "%s"\nshell = "no-such-shell"\n' % other, encoding="utf-8")
    c = ctx(run(tmp_path))
    assert "OUT OF DATE" in c and "saved OS " + other in c and "Ask the user before updating" in c


def test_personal_profile_rendered(tmp_path):
    c = ctx(run(tmp_path, personal=PERSONAL))
    assert "Decisions: numbered list" in c
    assert "Reply length: short" in c
    assert "CI: hosted (budget 3000 minutes/month: conserve)" in c
    assert "noreply" in c
    assert "Alerts go to: slack (issues only)" in c
    assert "Always confirm before: production restarts, anything deleting data" in c


def test_no_personal_profile_says_defaults(tmp_path):
    assert "no personal profile configured" in ctx(run(tmp_path))


def test_remote_session_skips_machine_file(tmp_path):
    c = ctx(run(tmp_path, remote=True))
    assert "Cloud/web session" in c and "NO MACHINE PROFILE" not in c


def test_minimal_toml_parser_without_tomllib(monkeypatch):
    mod = load_module()
    real_import = builtins.__import__

    def no_tomllib(name, *a, **k):
        if name == "tomllib":
            raise ModuleNotFoundError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_tomllib)
    d = mod.parse_toml(PERSONAL)
    assert d["communication"]["decisions"] == "numbered"
    assert d["delivery"]["hosted_minutes_budget"] == 3000
    assert d["alerts"]["issues_only"] is True
    assert d["safety"]["always_confirm"] == ["production restarts", "anything deleting data"]


def test_saved_gpu_skips_the_probe(tmp_path, monkeypatch, capsys):
    """The GPU probe spawns a process; once the machine file records the GPU it must not run."""
    import io
    mod = load_module()
    d = tmp_path / "gk"
    d.mkdir()
    shell = (mod.detect_shells(mod.detect_os()) or ["bash"])[0]
    (d / "machine.toml").write_text('os = "%s"\nshell = "%s"\ngpu = "Example GPU (4 GB)"\n'
                                    % (mod.detect_os(), shell), encoding="utf-8")
    for k in list(os.environ):
        if k.startswith(("CLAUDE_PLUGIN_OPTION_", "GNOMONKIT_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GNOMONKIT_HOME", str(d))
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    monkeypatch.setattr(sys, "argv", ["session_profile.py"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

    def probe():
        raise AssertionError("GPU probed although the machine file has it")

    monkeypatch.setattr(mod, "detect_gpu", probe)
    assert mod.main() == 0
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "GPU: Example GPU (4 GB)" in out


# ---- the default profile location, first-run setup and the disclaimer ----------------------------
NOTICE = "FIRST-RUN SETUP"
REMINDER = "run setup any time"


def onboarding(tmp_path):
    p = tmp_path / "gk" / "onboarding.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def write_default_profile(tmp_path, text):
    d = tmp_path / "gk"
    d.mkdir(exist_ok=True)
    (d / "personal.toml").write_text(text, encoding="utf-8")


def test_the_default_profile_needs_no_overlay(tmp_path):
    write_default_profile(tmp_path, PERSONAL)
    c = ctx(run(tmp_path, source="startup"))
    assert "Decisions: numbered list" in c
    assert "no personal profile configured" not in c and NOTICE not in c


def test_the_option_wins_over_the_variable_and_the_default(tmp_path):
    write_default_profile(tmp_path, '[communication]\nreply_length = "detailed"\n')
    other = tmp_path / "other.toml"
    other.write_text('[communication]\nreply_length = "short"\n', encoding="utf-8")
    option = tmp_path / "option.toml"
    option.write_text('[communication]\nreply_length = "normal"\n', encoding="utf-8")
    c = ctx(run(tmp_path, env_extra={"GNOMONKIT_PERSONAL_PROFILE": str(other)}))
    assert "Reply length: short" in c
    c = ctx(run(tmp_path, env_extra={"GNOMONKIT_PERSONAL_PROFILE": str(other),
                                     "CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE": str(option)}))
    assert "Reply length: normal" in c
    # recorded for `gnomonkit profile set`, which can't see plugin options; cleared once it is unset
    assert onboarding(tmp_path)["profile_option"] == str(option)
    ctx(run(tmp_path))
    assert "profile_option" not in onboarding(tmp_path)


def test_setup_is_offered_on_startup_only_and_at_most_twice(tmp_path):
    for source in ("resume", "compact", "clear"):
        assert NOTICE not in ctx(run(tmp_path, source=source))
    assert onboarding(tmp_path).get("offers") is None
    c = ctx(run(tmp_path, source="startup"))
    assert NOTICE in c and "onboarding" in c and "setup skipped" in c and "Offer 1 of 2" in c
    assert NOTICE in ctx(run(tmp_path, source="startup"))
    c = ctx(run(tmp_path, source="startup"))
    assert NOTICE not in c and REMINDER in c
    assert onboarding(tmp_path)["offers"] == 2


def test_setup_is_never_offered_where_nobody_is_watching(tmp_path):
    assert NOTICE not in ctx(run(tmp_path, source="startup", env_extra={"GNOMONKIT_NO_ONBOARDING": "1"}))
    assert NOTICE not in ctx(run(tmp_path, source="startup", remote=True))
    assert onboarding(tmp_path).get("offers") is None


@pytest.mark.parametrize("state", ["done", "skipped"])
def test_done_or_skipped_stops_the_offer_and_the_reminder(tmp_path, state):
    d = tmp_path / "gk"
    d.mkdir()
    (d / "onboarding.json").write_text(json.dumps({"state": state, "offers": 2}), encoding="utf-8")
    c = ctx(run(tmp_path, source="startup"))
    assert NOTICE not in c and REMINDER not in c


def test_a_profile_anywhere_means_no_offer(tmp_path):
    assert NOTICE not in ctx(run(tmp_path, source="startup", personal=PERSONAL))


def test_the_disclaimer_line_stays_until_acknowledged(tmp_path):
    c = ctx(run(tmp_path, personal=PERSONAL))
    assert "provided as is: see DISCLAIMER.md" in c
    c = ctx(run(tmp_path, personal=PERSONAL + '\n[acknowledged]\ndisclaimer = "1"\n'))
    assert "DISCLAIMER.md" not in c


def test_the_newer_keys_each_get_a_line(tmp_path):
    text = PERSONAL.replace("[delivery]\n", '[delivery]\nprs = "always"\ntest_style = "test-first"\n', 1)
    text = text.replace("[communication]\n", '[communication]\nexplanation_level = "teach"\n', 1)
    text += ('\n[working]\nask_before_acting = "always"\nsubagents = "sparing"\n'
             '[code]\nstyle = "small functions, early returns"\n'
             '[memory]\nlessons = "off"\n')
    c = ctx(run(tmp_path, personal=text))
    for want in ("Ask before acting: always", "Subagents: sparing", "Code style: small functions, early returns",
                 "Pull requests: always", "Tests: write the failing test first", "Explanations: teach",
                 "Lessons from past sessions: off"):
        assert want in c


@pytest.mark.parametrize("table, want, absent", [
    ('merge = "agent"\n', "Merging: merge your own pull request once it is green; don't ask", "ask the user"),
    ('merge = "ask"\n', "Merging: ask the user before merging any pull request", "once it is green"),
    ('merge = "ask-for"\nmerge_ask_for = ["docs/**", "label:release"]\n',
     "but ask the user first when it changes a file matching, or carries a label in: docs/**, label:release", None),
    ('merge = "sometimes"\n', None, "Merging"),
])
def test_the_merge_setting_gets_a_line_the_skills_read(tmp_path, table, want, absent):
    c = ctx(run(tmp_path, personal=PERSONAL.replace("[delivery]\n", "[delivery]\n" + table, 1)))
    if want:
        assert want in c
    if absent:
        assert absent not in c


def test_a_value_outside_the_choices_is_left_out(tmp_path):
    c = ctx(run(tmp_path, personal='[working]\nask_before_acting = "whenever"\n'))
    assert "Ask before acting" not in c


def test_an_unreadable_profile_is_named_and_defaults_apply(tmp_path):
    c = ctx(run(tmp_path, personal="[communication\nbroken =\n"))
    assert "can't be read" in c and "profile show" in c


def test_write_machine_keeps_keys_it_does_not_set(tmp_path):
    mod = load_module()
    shells = mod.detect_shells(mod.detect_os())
    if not shells:
        pytest.skip("no known shell on this test machine")
    d = tmp_path / "gk"
    d.mkdir()
    (d / "machine.toml").write_text('note = "kept"\nshell = "old"\n[extra]\nx = 1\n', encoding="utf-8")
    assert run(tmp_path, "--write-machine", "--shell", shells[0]).returncode == 0
    text = (d / "machine.toml").read_text(encoding="utf-8")
    data = mod.parse_toml(text)
    assert data["shell"] == shells[0] and data["note"] == "kept" and data["extra"] == {"x": 1}
