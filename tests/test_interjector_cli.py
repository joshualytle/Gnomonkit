"""Interjector CLI: the user writes comment/block/hold notes into a session's inbox."""
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "interjector"))
sys.path.insert(0, str(ROOT / "plugins" / "interjector" / "hooks"))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDECODE", raising=False)   # the user at a terminal: no grant check
    import importlib
    import gk_grants
    monkeypatch.setattr(gk_grants, "at_terminal", lambda stdin=None, stdout=None: True)   # pytest has no console
    import ij_store
    importlib.reload(ij_store)
    import interjector.__main__ as cli
    importlib.reload(cli)
    return tmp_path, ij_store, cli


def _session(store, sid, when):
    store.append_event(sid, {"event": "PreToolUse", "tool": "Bash", "target": "ls"})
    p = store.session_dir(sid) / "events.jsonl"
    os.utime(p, (when, when))


def _inbox(store, sid):
    return [json.loads(p.read_text(encoding="utf-8")) for p in (store.session_dir(sid) / "inbox").glob("*.json")]


def test_say_block_hold_write_user_notes(home):
    _, store, cli = home
    _session(store, "s1", time.time())
    assert cli.main(["say", "--session", "s1", "check the tests"]) == 0
    assert cli.main(["block", "--session", "s1", "--match", r"\brm\b", "list first"]) == 0
    assert cli.main(["hold", "--session", "s1", "update CHANGES.md before stopping"]) == 0
    notes = sorted(_inbox(store, "s1"), key=lambda n: n["created"])
    assert [(n["kind"], n["source"]) for n in notes] == [("comment", "user"), ("block", "user"), ("hold", "user")]
    assert notes[1]["match"] == r"\brm\b"


def test_latest_resolves_to_the_newest_session(home):
    _, store, cli = home
    _session(store, "old", time.time() - 600)
    _session(store, "new", time.time())
    assert cli.main(["say", "--session", "latest", "hi"]) == 0
    assert len(_inbox(store, "new")) == 1 and _inbox(store, "old") == []


def test_unknown_session_exits_2(home, capsys):
    _, store, cli = home
    assert cli.main(["say", "--session", "nope", "hi"]) == 2
    assert "no such session" in capsys.readouterr().err


def test_block_requires_match(home):
    _, store, cli = home
    _session(store, "s1", time.time())
    with pytest.raises(SystemExit):
        cli.main(["block", "--session", "s1", "no pattern"])


def test_sessions_lists_pending_counts(home, capsys):
    _, store, cli = home
    _session(store, "s1", time.time())
    store.write_note("s1", "comment", "x", "user")
    assert cli.main(["sessions"]) == 0
    out = capsys.readouterr().out
    assert "s1" in out and "1 pending" in out
