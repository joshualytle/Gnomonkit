"""The gnomonkit command's dispatcher and its shared helpers (plugins/core/gnomonkit_cmds/_common.py).

Synthetic data only: made-up profiles and folders under pytest's tmp_path.
"""
import builtins
import io
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "plugins", "core")
sys.path.insert(0, CORE)

from gnomonkit_cmds import _common as C  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11: the fallback parser is the only reader
    tomllib = None


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Never read or write the real ~/.gnomonkit, or a profile the developer's environment names."""
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk-isolated"))
    for k in list(os.environ):
        if k.startswith("CLAUDE_PLUGIN_OPTION_") or k in ("GNOMONKIT_PERSONAL_PROFILE", "GNOMONKIT_CONTEXT_BUDGET"):
            monkeypatch.delenv(k, raising=False)
    C._CACHE.clear()
    yield
    C._CACHE.clear()


# ---- dispatcher ----------------------------------------------------------------------------------
def _copy_core(tmp_path):
    dst = tmp_path / "core"
    (dst / ".claude-plugin").mkdir(parents=True)
    shutil.copy(os.path.join(CORE, "gnomonkit.py"), dst / "gnomonkit.py")
    shutil.copy(os.path.join(CORE, ".claude-plugin", "plugin.json"), dst / ".claude-plugin" / "plugin.json")
    shutil.copytree(os.path.join(CORE, "gnomonkit_cmds"), dst / "gnomonkit_cmds",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def _run(script, *args, env=None, stdin=""):
    return subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                          input=stdin, env=env, encoding="utf-8")


def test_a_new_module_becomes_a_subcommand_without_editing_the_entry_point(tmp_path):
    core = _copy_core(tmp_path)
    (core / "gnomonkit_cmds" / "hello.py").write_text(
        "def register(sub):\n"
        "    p = sub.add_parser('hello', help='say hello')\n"
        "    p.add_argument('name')\n"
        "    p.set_defaults(func=lambda a: print('hello ' + a.name) or 0)\n", encoding="utf-8")
    (core / "gnomonkit_cmds" / "_private_helper.py").write_text(
        "def register(sub):\n    raise SystemExit('underscore modules are never registered')\n", encoding="utf-8")
    r = _run(core / "gnomonkit.py", "hello", "world")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "hello world"
    assert "hello" in _run(core / "gnomonkit.py", "--help").stdout


def test_a_broken_module_is_reported_and_the_rest_still_work(tmp_path):
    core = _copy_core(tmp_path)
    (core / "gnomonkit_cmds" / "aaa_broken.py").write_text("import no_such_module_here\n", encoding="utf-8")
    (core / "gnomonkit_cmds" / "zzz_ok.py").write_text(
        "def register(sub):\n"
        "    sub.add_parser('zzz').set_defaults(func=lambda a: 0)\n", encoding="utf-8")
    r = _run(core / "gnomonkit.py", "zzz")
    assert r.returncode == 0
    assert "aaa_broken" in r.stderr and "could not load" in r.stderr


def test_command_error_exits_with_its_code(tmp_path):
    core = _copy_core(tmp_path)
    (core / "gnomonkit_cmds" / "refuse.py").write_text(
        "from gnomonkit_cmds._common import CommandError\n"
        "def _go(a):\n    raise CommandError('no, and here is why')\n"
        "def register(sub):\n    sub.add_parser('refuse').set_defaults(func=_go)\n", encoding="utf-8")
    r = _run(core / "gnomonkit.py", "refuse")
    assert r.returncode == 2 and "no, and here is why" in r.stderr


def test_shipped_modules_register_setup_profile_and_launcher():
    r = _run(os.path.join(CORE, "gnomonkit.py"), "--help")
    assert r.returncode == 0 and r.stderr == ""
    for name in ("setup", "profile", "install-launcher"):
        assert name in r.stdout


def test_every_setting_is_documented_and_the_skill_is_mapped():
    """PROFILES.md lists the schema, and the onboarding skill is routed: neither may drift from the code."""
    with open(os.path.join(ROOT, "docs", "PROFILES.md"), encoding="utf-8") as fh:
        doc = fh.read()
    missing = [s.key for s in C.SCHEMA if "| `%s` |" % s.key not in doc]
    assert not missing, "add these to the settings table in docs/PROFILES.md: %s" % missing
    with open(os.path.join(CORE, "hooks", "skill_map.yaml"), encoding="utf-8") as fh:
        assert "    - onboarding\n" in fh.read()
    with open(os.path.join(CORE, "skills", "onboarding", "SKILL.md"), encoding="utf-8") as fh:
        skill = fh.read()
    assert "profile set" in skill and "setup security" in skill and "setup done" in skill


# ---- TOML ------------------------------------------------------------------------------------------
SAMPLE = {
    "top": 3,
    "communication": {"decisions": "prose", "note": 'quote " backslash \\ hash # tab \t end'},
    "safety": {"always_confirm": ["one", "two, with a comma"]},
    "unknown_table": {"n": 1.5, "flag": True, "sub": {"q": "z"}},
    "trust": [{"root": "/tmp/example-a", "level": "trusted"}, {"root": "/tmp/example-b"}],
    "acknowledged": {"disclaimer": "1"},
}


def test_dumps_round_trips_through_both_readers():
    text = C.dumps(SAMPLE, C.personal_layout(), C.PERSONAL_HEADER)
    assert C._fallback_loads(text) == SAMPLE
    if tomllib:
        assert tomllib.loads(text) == SAMPLE


def test_dumps_puts_schema_tables_first_and_unknown_ones_after():
    text = C.dumps({"zeta": {"a": 1}, "acknowledged": {"disclaimer": "1"},
                    "communication": {"reply_length": "short", "decisions": "prose", "extra": "kept"}},
                   C.personal_layout())
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines.index("[communication]") < lines.index("[acknowledged]") < lines.index("[zeta]")
    # known keys in schema order, unknown keys after them
    i = lines.index("[communication]")
    assert lines[i + 1:i + 4] == ['decisions = "prose"', 'reply_length = "short"', 'extra = "kept"']


def test_fallback_parser_reads_hand_written_files(monkeypatch):
    real_import = builtins.__import__

    def no_tomllib(name, *a, **k):
        if name == "tomllib":
            raise ModuleNotFoundError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_tomllib)
    text = (
        "# a comment\n"
        "[communication]\n"
        "decisions = 'prose'   # literal string\n"
        "reply_length = \"short\"\n"
        "\n"
        "[safety]\n"
        "always_confirm = [\n"
        "  \"deleting data\",   # why\n"
        "  \"publishing anything\",\n"
        "]\n"
        "[delivery]\n"
        "hosted_minutes_budget = 3_000\n"
        "[a.b]\n"
        "c = -2\n")
    d = C.parse_toml(text)
    assert d["communication"] == {"decisions": "prose", "reply_length": "short"}
    assert d["safety"]["always_confirm"] == ["deleting data", "publishing anything"]
    assert d["delivery"]["hosted_minutes_budget"] == 3000
    assert d["a"] == {"b": {"c": -2}}
    with pytest.raises(ValueError):
        C.parse_toml("[communication]\ndecisions = \"unterminated\n")


# ---- schema ------------------------------------------------------------------------------------
def S(key):
    return C.SCHEMA_BY_KEY[key]


def test_coerce_accepts_valid_values_and_names_the_choices_otherwise():
    assert C.coerce(S("communication.reply_length"), "Short") == "short"
    with pytest.raises(ValueError) as e:
        C.coerce(S("communication.reply_length"), "tiny")
    assert "short | normal | detailed" in str(e.value)
    assert C.coerce(S("context.context_budget"), "300k") == 300000
    assert C.coerce(S("context.context_budget"), "250_000") == 250000
    with pytest.raises(ValueError):
        C.coerce(S("context.context_budget"), "-5")
    assert C.coerce(S("alerts.issues_only"), "yes") is True
    assert C.coerce(S("safety.always_confirm"), "deleting data, publishing anything") == \
        ["deleting data", "publishing anything"]
    assert C.coerce(S("safety.always_confirm"), '["one, two", "three"]') == ["one, two", "three"]
    assert C.coerce(S("corrections.repo"), "example-org/overlay") == "example-org/overlay"
    with pytest.raises(ValueError):
        C.coerce(S("corrections.repo"), "not a repo")
    assert C.coerce(S("plan.monthly_usd"), "$20") == 20
    with pytest.raises(ValueError):
        C.coerce(S("code.style"), "two\nlines")
    with pytest.raises(ValueError):
        C.coerce(S("acknowledged.disclaimer"), "99")


def test_scratch_root_refuses_a_root_or_the_home_folder(tmp_path):
    root = os.path.abspath(os.sep)
    with pytest.raises(ValueError):
        C.coerce(S("paths.scratch_root"), root)
    with pytest.raises(ValueError):
        C.coerce(S("paths.scratch_root"), "~")
    assert C.coerce(S("paths.scratch_root"), str(tmp_path / "scratch")) == C.resolve_path(str(tmp_path / "scratch"))


def test_validate_profile_lists_problems_and_allows_unknown_keys():
    assert C.validate_profile({"communication": {"decisions": "numbered", "mine": 1}, "other": {"x": 1}}) == []
    problems = C.validate_profile({"communication": {"decisions": "loud"}, "context": {"context_budget": "big"}})
    assert len(problems) == 2 and any("decisions" in p for p in problems)
    assert C.validate_profile({"safety": "not a table"})


# ---- paths -------------------------------------------------------------------------------------
def test_normalise_path_resolves_dots_and_unifies_separators(tmp_path):
    a = tmp_path / "x" / "y"
    a.mkdir(parents=True)
    messy = str(tmp_path) + "/x/../x/y/"
    assert C.normalise_path(messy) == C.normalise_path(str(a))
    if os.name == "nt":
        assert "/" not in C.normalise_path(messy)
        assert C.normalise_path(str(a).upper()) == C.normalise_path(str(a))
    assert C.path_within(str(a), str(tmp_path))
    assert not C.path_within(str(tmp_path) + "-sibling", str(tmp_path))


def test_normalise_path_resolves_symlinks(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted here")
    assert C.normalise_path(str(link)) == C.normalise_path(str(target))


# ---- files -------------------------------------------------------------------------------------
def test_atomic_write_replaces_and_leaves_no_temp_file(tmp_path):
    p = tmp_path / "gk" / "nested" / "data.json"
    C.atomic_write(str(p), "one\n")
    C.atomic_write(str(p), "two\n")
    assert p.read_text(encoding="utf-8") == "two\n"
    assert sorted(os.listdir(p.parent)) == ["data.json"]
    if os.name != "nt":
        assert (p.stat().st_mode & 0o777) == 0o600
        assert (p.parent.stat().st_mode & 0o777) == 0o700
        assert ((tmp_path / "gk").stat().st_mode & 0o777) == 0o700


# ---- a person at a terminal --------------------------------------------------------------------
class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def test_require_tty_refuses_a_pipe():
    with pytest.raises(C.CommandError) as e:
        C.require_tty("grant", stdin=io.StringIO("grant\n"), stdout=io.StringIO())
    assert e.value.code == 2 and "terminal" in str(e.value)


def test_the_null_device_is_not_a_terminal():
    """On Windows isatty() is True for NUL, so `< NUL > NUL` would pass an isatty-only check."""
    with open(os.devnull) as fin, open(os.devnull, "w") as fout:
        if os.name == "nt":
            assert fin.isatty() and fout.isatty()   # the case the console check exists for
        assert C.is_terminal(fin, fout) is False
        with pytest.raises(C.CommandError):
            C.require_tty("grant", stdin=fin, stdout=fout)


def test_on_windows_a_terminal_must_be_a_console_on_both_sides(monkeypatch):
    monkeypatch.setattr(C, "_CHECK_CONSOLE", True)
    tin, tout = FakeTTY(), FakeTTY()
    assert C.is_terminal(tin, tout) is False   # no console handle behind an in-memory stream
    for consoles, want in (((), False), ((tin,), False), ((tout,), False), ((tin, tout), True)):
        monkeypatch.setattr(C, "_is_console", lambda s, c=consoles: s in c)
        assert C.is_terminal(tin, tout) is want
    monkeypatch.setattr(C, "_CHECK_CONSOLE", False)   # elsewhere isatty() is the check
    monkeypatch.setattr(C, "_is_console", lambda s: False)
    assert C.is_terminal(tin, tout) is True


def test_require_tty_needs_the_word_typed(monkeypatch):
    monkeypatch.setattr(C, "_is_console", lambda stream: True)   # stands in for a console on Windows
    out = FakeTTY()
    C.require_tty("grant", stdin=FakeTTY("grant\n"), stdout=out)
    assert "grant" in out.getvalue()
    with pytest.raises(C.CommandError) as e:
        C.require_tty("grant", stdin=FakeTTY("yes\n"), stdout=FakeTTY())
    assert e.value.code == 2
    with pytest.raises(C.CommandError):
        C.require_tty("grant", stdin=FakeTTY(""), stdout=FakeTTY())   # end of input
    with pytest.raises(C.CommandError):
        C.require_tty("grant", stdin=FakeTTY("grant\n"), stdout=io.StringIO())   # stdout piped


# ---- precedence ----------------------------------------------------------------------------------
def test_setting_precedence_option_then_profile_then_default(tmp_path, monkeypatch):
    for k in ("CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET", "GNOMONKIT_CONTEXT_BUDGET",
              "CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE", "GNOMONKIT_PERSONAL_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    C._CACHE.clear()
    assert C.setting("context.context_budget") == (250000, "default")
    C.atomic_write(C.default_personal_path(), "[context]\ncontext_budget = 400000\n")
    C._CACHE.clear()
    assert C.setting("context.context_budget") == (400000, "personal profile")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET", "100000")
    assert C.setting("context.context_budget") == ("100000", "CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET")
    C._CACHE.clear()


def test_personal_profile_path_order(tmp_path, monkeypatch):
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    for k in ("CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE", "GNOMONKIT_PERSONAL_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    assert C.personal_profile_path() == (str(tmp_path / "gk" / "personal.toml"), "default location")
    env_file = str(tmp_path / "env.toml")
    monkeypatch.setenv("GNOMONKIT_PERSONAL_PROFILE", env_file)
    assert C.personal_profile_path()[0] == env_file
    recorded = str(tmp_path / "overlay" / "profile" / "personal.toml")
    C.write_onboarding({"profile_option": recorded})
    assert C.personal_profile_path()[0] == recorded
    assert C.personal_profile_path(use_record=False)[0] == env_file
    opt = str(tmp_path / "opt.toml")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE", opt)
    assert C.personal_profile_path() == (opt, "plugin option personal_profile")
