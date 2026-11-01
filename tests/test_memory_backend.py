"""This machine's kind and the memory backend found on it (plugins/core/gnomonkit_cmds/_common.py:
machine_kind, mempalace_status), and the auto default of Gnomonkit's own history index and lessons
that follows from it (memory_feature).

Synthetic data only: a made-up home folder, Claude config folder and PATH under pytest's tmp_path, a fake
`mempalace` executable and made-up .claude.json / .mcp.json files. The developer's own config and any
real MemPalace install are never looked at: PATH holds only an empty bin folder, and HOME, USERPROFILE,
CLAUDE_CONFIG_DIR and GNOMONKIT_HOME all point into tmp_path.
"""
import json
import os
import sys
import time
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "plugins", "core")
sys.path.insert(0, CORE)

import gnomonkit  # noqa: E402
from gnomonkit_cmds import _common as C  # noqa: E402
from gnomonkit_cmds import _history as H  # noqa: E402
from gnomonkit_cmds import _lessons as L  # noqa: E402

EMPTY = {"installed": False, "mcp": False, "where": []}


@pytest.fixture
def here(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("CLAUDE_PLUGIN_OPTION_", "GNOMONKIT_")) or k == "CLAUDE_CONFIG_DIR":
            monkeypatch.delenv(k, raising=False)
    ns = types.SimpleNamespace(home=tmp_path / "home", cfg=tmp_path / "claude-config", bin=tmp_path / "bin",
                               work=tmp_path / "work", gk=tmp_path / "gk")
    for d in (ns.home, ns.cfg, ns.bin, ns.work):
        d.mkdir()
    monkeypatch.setenv("HOME", str(ns.home))
    monkeypatch.setenv("USERPROFILE", str(ns.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(ns.cfg))
    monkeypatch.setenv("GNOMONKIT_HOME", str(ns.gk))
    monkeypatch.setenv("PATH", str(ns.bin))
    monkeypatch.chdir(ns.work)
    C._CACHE.clear()
    assert C.claude_json_path() == str(ns.cfg / ".claude.json")
    yield ns
    C._CACHE.clear()


def fake_mempalace(bin_dir):
    if os.name == "nt":
        p = bin_dir / "mempalace.cmd"
        p.write_text("@echo off\r\n", encoding="ascii")
    else:
        p = bin_dir / "mempalace"
        p.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
        p.chmod(0o755)
    return p


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def look(ns, folder=None):
    return C.mempalace_status(cwd=str(folder or ns.work), refresh=True)


# ---- what counts -----------------------------------------------------------------------------------
def test_nothing_set_up_and_a_hook_does_not_count(here):
    write_json(here.cfg / "settings.json", {"hooks": {"SessionStart": [
        {"hooks": [{"type": "command", "command": "mempalace hook run --example"}]}]}})
    write_json(here.cfg / ".claude.json", {"mcpServers": {"notes": {"command": "notes-server"}}})
    assert look(here) == EMPTY


def test_an_executable_on_path_is_installed_but_not_set_up(here):
    fake_mempalace(here.bin)
    st = look(here)
    assert st["installed"] is True and st["mcp"] is False
    assert len(st["where"]) == 1 and st["where"][0].startswith("on PATH: ")


def test_servers_in_claude_json_at_the_top_and_in_every_project(here, tmp_path):
    app_a, app_b = str(tmp_path / "projects" / "app-a"), str(tmp_path / "projects" / "app-b")
    write_json(here.cfg / ".claude.json", {
        "mcpServers": {"Memory": {"command": "python", "args": ["-m", "MemPalace.mcp_server"]},
                       "notes": {"command": "notes-server"}},
        "projects": {app_a: {"mcpServers": {"MEMPALACE": {"command": "uvx", "args": ["example"]}}},
                     app_b: {"mcpServers": {"remote": {"type": "http", "url": "https://example.invalid/mempalace"}}},
                     "broken": "not a table"}})
    st = look(here)
    assert st["installed"] is False and st["mcp"] is True
    assert len(st["where"]) == 3
    assert any(w.endswith("mcpServers.Memory") for w in st["where"])
    assert any(app_a in w and w.endswith(".mcpServers.MEMPALACE") for w in st["where"])
    assert any(app_b in w and w.endswith(".mcpServers.remote") for w in st["where"])
    assert not any("notes" in w for w in st["where"])


def test_mcp_json_in_the_folder_and_at_its_repo_root(here, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "pkg"
    sub.mkdir()
    write_json(repo / ".mcp.json", {"mcpServers": {"palace": {"command": "MemPalace-MCP"}}})
    st = look(here, sub)
    assert st["mcp"] is True and st["where"] == ["%s: palace" % (repo / ".mcp.json")]
    write_json(sub / ".mcp.json", {"mcpServers": {"memory": {"command": "mempalace", "args": ["mcp"]}}})
    assert len(look(here, sub)["where"]) == 2
    assert look(here)["mcp"] is False   # another folder, outside that repo


def test_a_plugin_that_brings_the_server(here, tmp_path):
    bare, wrapped, inline = (tmp_path / "plugin-cache" / n / "1.0.0" for n in ("bare", "wrapped", "inline"))
    write_json(bare / ".mcp.json", {"mempalace": {"command": "python", "args": ["-m", "example"]}})
    write_json(wrapped / ".mcp.json", {"mcpServers": {"recall": {"command": "mempalace-mcp"}}})
    write_json(inline / ".claude-plugin" / "plugin.json", {"name": "inline", "mcpServers": {
        "palace": {"command": "MEMPALACE"}}})
    write_json(here.cfg / "plugins" / "installed_plugins.json", {"version": 2, "plugins": {
        "bare@example-market": [{"scope": "user", "installPath": str(bare), "version": "1.0.0"}],
        "wrapped@example-market": [{"scope": "project", "installPath": str(wrapped), "version": "1.0.0"}],
        "inline@example-market": [{"scope": "user", "installPath": str(inline)}],
        "gone@example-market": [{"scope": "user", "installPath": str(tmp_path / "not-there")}]}})
    st = look(here)
    assert st["mcp"] is True
    assert sorted(st["where"]) == ["plugin bare@example-market (.mcp.json): mempalace",
                                   "plugin inline@example-market (plugin.json): palace",
                                   "plugin wrapped@example-market (.mcp.json): recall"]


def test_without_a_config_dir_it_reads_the_home_folder(here, monkeypatch, tmp_path):
    write_json(here.cfg / ".claude.json", {"mcpServers": {"mempalace": {"command": "x"}}})   # not read now
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert C.claude_json_path() == str(here.home / ".claude.json")
    assert look(here) == EMPTY
    write_json(here.home / ".claude.json", {"projects": {str(tmp_path / "app"): {"mcpServers": {
        "palace": {"command": "mempalace", "args": ["mcp"]}}}}})
    plug = tmp_path / "plugin-cache" / "memory"
    write_json(plug / ".mcp.json", {"mcpServers": {"memory": {"command": "mempalace"}}})
    write_json(here.home / ".claude" / "plugins" / "installed_plugins.json",
               {"version": 2, "plugins": {"memory@example-market": [{"installPath": str(plug)}]}})
    assert len(look(here)["where"]) == 2


# ---- the cache ---------------------------------------------------------------------------------------
def test_the_answer_is_cached_for_an_hour_per_folder(here):
    assert C.memory_backend_path() == str(here.gk / "memory-backend.json")
    assert C.mempalace_status(cwd=str(here.work)) == EMPTY
    fake_mempalace(here.bin)
    assert C.mempalace_status(cwd=str(here.work))["installed"] is False                # from the cache
    assert C.mempalace_status(cwd=str(here.work), refresh=True)["installed"] is True   # looked again
    cache = json.loads((here.gk / "memory-backend.json").read_text(encoding="utf-8"))
    assert cache["version"] == 1 and list(cache["entries"]) == [C.normalise_path(str(here.work))]

    # An entry older than an hour is looked at again; a fresh one is used as it is.
    key = C.normalise_path(str(here.work))
    stale = {"installed": False, "mcp": True, "where": ["made up"]}
    for age, want in ((10, stale), (C.MEMPALACE_CACHE_SECONDS + 5, None)):
        write_json(here.gk / "memory-backend.json",
                   {"version": 1, "entries": {key: {"at": time.time() - age, "status": stale}}})
        got = C.mempalace_status(cwd=str(here.work))
        assert got == want if want else (got["installed"] is True and got["mcp"] is False)


def test_broken_files_never_raise(here, monkeypatch, tmp_path):
    write_json(here.cfg / ".claude.json", "{not json")
    write_json(here.work / ".mcp.json", "[1, 2]")
    write_json(here.cfg / "plugins" / "installed_plugins.json", {"plugins": {"a": "x", "b": [5, {"installPath": 7}]}})
    write_json(here.gk / "memory-backend.json", "garbage")
    assert C.mempalace_status(cwd=str(here.work)) == EMPTY
    write_json(here.cfg / ".claude.json", {"mcpServers": ["mempalace"], "projects": ["x"]})
    write_json(here.work / ".mcp.json", {"mcpServers": "mempalace"})
    assert look(here) == EMPTY
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("GNOMONKIT_HOME", str(blocker))   # the cache can't be written: still an answer
    assert look(here) == EMPTY

    def boom(_cwd):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(C, "_detect_mempalace", boom)
    assert look(here) == EMPTY


# ---- the machine's kind ------------------------------------------------------------------------------
def test_the_kind_comes_from_the_machine_profile_only(here):
    assert C.machine_kind() is None and C.is_personal_machine() is False
    write_json(here.gk / "personal.toml", '[machine]\nkind = "personal"\n')   # synced from elsewhere: ignored
    C._CACHE.clear()
    assert C.machine_kind() is None
    for text, want in (('kind = "work"\n', "work"), ('kind = "unknown"\n', "unknown"),
                       ('kind = "personal"\n', "personal"), ('kind = "maybe"\n', None), ("[[[ not toml", None)):
        write_json(here.gk / "machine.toml", text)
        C._CACHE.clear()
        assert C.machine_kind() == want
        assert C.is_personal_machine() is (want == "personal")


# ---- auto: Gnomonkit's own history index and lessons stand aside for MemPalace ---------------------
FEATURES = (("memory.history", "GNOMONKIT_HISTORY"), ("memory.lessons", "GNOMONKIT_LESSONS"))


def modes(ns):
    return [C.memory_feature(key, env, str(ns.work))[0] for key, env in FEATURES]


def profile(ns, text):
    write_json(ns.gk / "personal.toml", text)
    C._CACHE.clear()


def cli(capsys, *argv):
    rc = gnomonkit.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def test_auto_is_the_default_and_on_without_mempalace(here):
    assert C.SCHEMA_BY_KEY["memory.history"].default == "auto"
    assert C.SCHEMA_BY_KEY["memory.lessons"].default == "auto"
    assert modes(here) == [True, True]
    assert H.due(cwd=str(here.work)) is True and L.lessons_off(str(here.work)) is False
    assert C.memory_feature("memory.history", "GNOMONKIT_HISTORY") == (True, "auto, and MemPalace isn't set up here")


def test_auto_is_off_with_mempalace_installed(here):
    fake_mempalace(here.bin)
    assert modes(here) == [False, False]
    assert H.due(cwd=str(here.work)) is False
    assert H.run_index() == {"disabled": True}
    assert L.lessons_off(str(here.work)) is True
    assert not (here.gk / "history" / "history.db").exists()


def test_auto_is_off_with_mempalace_registered_as_an_mcp_server_only(here):
    write_json(here.cfg / ".claude.json", {"mcpServers": {"recall": {"command": "python",
                                                                     "args": ["-m", "mempalace.mcp_server"]}}})
    assert look(here)["installed"] is False
    assert modes(here) == [False, False]


def test_a_profile_auto_is_the_same_as_unset(here):
    fake_mempalace(here.bin)
    profile(here, '[memory]\nhistory = "auto"\nlessons = "auto"\n')
    assert modes(here) == [False, False]


def test_an_explicit_on_wins_over_an_installed_mempalace(here, monkeypatch):
    fake_mempalace(here.bin)
    monkeypatch.setenv("GNOMONKIT_HISTORY", "on")
    profile(here, '[memory]\nlessons = "on"\n')
    assert modes(here) == [True, True]
    assert C.memory_feature("memory.history", "GNOMONKIT_HISTORY", str(here.work)) == (True, "GNOMONKIT_HISTORY=on")
    assert H.due(cwd=str(here.work)) is True
    # the environment comes before the profile, both ways
    monkeypatch.setenv("GNOMONKIT_LESSONS", "off")
    assert L.lessons_off(str(here.work)) is True
    monkeypatch.delenv("GNOMONKIT_LESSONS")
    monkeypatch.delenv("GNOMONKIT_HISTORY")
    profile(here, '[memory]\nhistory = "off"\n')
    monkeypatch.setenv("GNOMONKIT_HISTORY", "1")
    assert modes(here)[0] is True


def test_an_explicit_off_needs_no_mempalace(here):
    profile(here, '[memory]\nhistory = "off"\nlessons = "off"\n')
    assert modes(here) == [False, False]
    assert C.memory_feature("memory.lessons", "GNOMONKIT_LESSONS")[1] == "memory.lessons = off in the personal profile"


def test_a_failed_detection_counts_as_no_mempalace(here, monkeypatch):
    fake_mempalace(here.bin)

    def boom(*_a, **_k):
        raise RuntimeError("synthetic")
    monkeypatch.setattr(C, "_detect_mempalace", boom)
    assert modes(here) == [True, True]
    monkeypatch.setattr(C, "mempalace_status", boom)
    assert modes(here) == [True, True]
    monkeypatch.setattr(C, "checked_profile_value", boom)
    assert modes(here) == [True, True]


def test_the_profile_takes_auto_and_rejects_anything_else(here, capsys):
    rc, _, _ = cli(capsys, "profile", "set", "memory.history=auto", "memory.lessons=on")
    assert rc == 0
    C._CACHE.clear()
    assert C.profile_value("memory.history") == "auto" and C.profile_value("memory.lessons") == "on"
    rc, _, err = cli(capsys, "profile", "set", "memory.history=sometimes")
    assert rc != 0 and "auto" in err


def test_the_commands_say_why_they_are_off(here, capsys):
    fake_mempalace(here.bin)
    rc, out, _ = cli(capsys, "history", "index")
    assert rc == 0 and "history indexing is off here (auto, and MemPalace is set up here)" in out
    assert "memory.history=on" in out
    rc, out, _ = cli(capsys, "history", "status")
    assert rc == 0 and "indexing: OFF (auto, and MemPalace is set up here)" in out
    rc, out, _ = cli(capsys, "lessons", "candidates")
    assert rc == 0 and out.startswith("lessons are off here (auto, and MemPalace is set up here)")
    rc, out, _ = cli(capsys, "lessons", "candidates", "--json")
    assert json.loads(out) == {"groups": [], "off": "auto, and MemPalace is set up here"}


def test_setup_says_gnomonkits_own_memory_is_off_beside_mempalace(here, capsys):
    write_json(here.gk / "machine.toml", 'kind = "personal"\n')
    write_json(here.cfg / ".claude.json", {"mcpServers": {"palace": {"command": "mempalace", "args": ["mcp"]}}})
    C._CACHE.clear()
    rc, out, _ = cli(capsys, "setup")
    assert rc == 0 and "Memory backend:    MemPalace, set up here (" in out
    assert "Gnomonkit's own memory.history, memory.lessons: off while MemPalace is set up (auto)" in out
    assert "memory.history=on memory.lessons=on" in out
    profile(here, '[memory]\nhistory = "on"\n')
    rc, out, _ = cli(capsys, "setup")
    assert "Gnomonkit's own memory.lessons: off while MemPalace is set up (auto)" in out
    profile(here, '[memory]\nhistory = "on"\nlessons = "on"\n')
    rc, out, _ = cli(capsys, "setup")
    assert "Gnomonkit's own" not in out
