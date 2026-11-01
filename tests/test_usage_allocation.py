"""`gnomonkit usage`: show reads, and budget, allow and lift change the allocation only for a person at a
terminal who types the word shown.

Synthetic data only: a made-up home, Claude config folder, projects and transcripts under pytest's tmp_path.
The hook and the count have their own script, plugins/core/hooks/test_usage_allocation.py.
"""
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "plugins" / "core"
for p in (str(CORE), str(CORE / "hooks")):
    if p not in sys.path:
        sys.path.insert(0, p)

import gk_grants as G  # noqa: E402
import gk_usage as U  # noqa: E402
import gnomonkit  # noqa: E402
from gnomonkit_cmds import _common as C  # noqa: E402
from gnomonkit_cmds import grants as GC  # noqa: E402

WORD = "confirm-0042"
SCRUB = ("GNOMONKIT_", "CLAUDE_PLUGIN_OPTION_")
SESSION_VARS = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID", "CLAUDE_CONFIG_DIR")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(SCRUB) or k in SESSION_VARS:
            monkeypatch.delenv(k, raising=False)
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("USERPROFILE", str(h))
    gk = h / ".gnomonkit"
    monkeypatch.setenv("GNOMONKIT_HOME", str(gk))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setattr(GC, "confirm_word", lambda kind="grant": WORD)
    return gk


@pytest.fixture
def work(tmp_path):
    root = tmp_path / "work"
    for name in ("alpha", "beta"):
        (root / name).mkdir(parents=True)
    return root


class Term(io.StringIO):
    def isatty(self):
        return True


class Pipe(io.StringIO):
    def isatty(self):
        return False


def at_terminal(monkeypatch, answers=""):
    out = Term()
    monkeypatch.setattr(C, "_is_console", lambda stream: True)
    monkeypatch.setattr(sys, "stdin", Term(answers))
    monkeypatch.setattr(sys, "stdout", out)
    return out


def cli(*argv):
    return gnomonkit.main(list(argv))


def child_env(home):
    e = {k: v for k, v in os.environ.items() if not k.startswith(SCRUB) and k not in SESSION_VARS}
    e.update(HOME=os.environ["HOME"], USERPROFILE=os.environ["USERPROFILE"], GNOMONKIT_HOME=str(home),
             CLAUDE_CONFIG_DIR=os.environ["CLAUDE_CONFIG_DIR"])
    return e


def transcript(folder, inp):
    d = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / "proj"
    d.mkdir(parents=True, exist_ok=True)
    line = {"type": "assistant", "cwd": str(folder),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "message": {"id": "m-%s-%d" % (Path(folder).name, inp), "model": "claude-opus-5-5",
                        "usage": {"input_tokens": inp, "output_tokens": 0}}}
    with open(d / "s1.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")


def audit():
    with open(G.audit_path("usage"), encoding="utf-8") as fh:
        return [json.loads(x) for x in fh]


@pytest.mark.parametrize("argv", [["usage", "budget", "500", "usd"], ["usage", "allow", "/x/alpha", "70"],
                                  ["usage", "lift", "/x/alpha"]])
def test_writing_commands_refuse_through_a_pipe(argv, home):
    """What an agent's shell looks like: stdin and stdout are pipes."""
    r = subprocess.run([sys.executable, str(CORE / "gnomonkit.py"), *argv], input="%s\n" % WORD,
                       capture_output=True, text=True, encoding="utf-8", env=child_env(home),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 2, r.stderr
    assert "terminal" in r.stderr and "refused" in r.stderr
    assert not os.path.exists(U.alloc_path())


def test_a_terminal_on_one_side_only_is_not_enough(home, monkeypatch):
    monkeypatch.setattr(sys, "stdin", Pipe(WORD + "\n"))
    monkeypatch.setattr(sys, "stdout", Term())
    assert cli("usage", "budget", "500", "usd") == 2
    assert not os.path.exists(U.alloc_path())


def test_budget_allow_and_lift_at_a_terminal(home, monkeypatch, work):
    at_terminal(monkeypatch, WORD + "\n")
    assert cli("usage", "budget", "1000", "tokens") == 0
    at_terminal(monkeypatch, WORD + "\n")
    assert cli("usage", "allow", str(work / "alpha"), "70") == 0
    at_terminal(monkeypatch, WORD + "\n")
    assert cli("usage", "allow", str(work / "beta"), "20%") == 0
    a = U.load()
    assert a.ok and a.budget == 1000 and a.unit == "tokens"
    assert [(p["folder"], p["share"]) for p in a.projects] == [(str(work / "alpha"), 70), (str(work / "beta"), 20)]
    before = Path(U.alloc_path()).read_text(encoding="utf-8")
    at_terminal(monkeypatch, WORD + "\n")
    assert cli("usage", "lift", str(work / "alpha")) == 0
    a = U.load()
    assert a.lifted(str(work / "alpha"), U.week_id()) and not a.lifted(str(work / "beta"), U.week_id())
    assert Path(U.alloc_path() + ".bak").read_text(encoding="utf-8") == before
    assert [x["event"] for x in audit()] == ["usage-budget", "usage-allow", "usage-allow", "usage-lift"]
    at_terminal(monkeypatch, WORD + "\n")
    assert cli("usage", "allow", str(work / "beta"), "none") == 0
    assert [p["folder"] for p in U.load().projects] == [str(work / "alpha")]


def test_the_wrong_word_changes_nothing(home, monkeypatch):
    at_terminal(monkeypatch, "confirm-9999\n")
    assert cli("usage", "budget", "500", "usd") == 2
    assert not os.path.exists(U.alloc_path())


def test_shares_above_100_are_refused(home, monkeypatch, work):
    at_terminal(monkeypatch, WORD + "\n")
    assert cli("usage", "allow", str(work / "alpha"), "70") == 0
    before = Path(U.alloc_path()).read_text(encoding="utf-8")
    at_terminal(monkeypatch, WORD + "\n")
    assert cli("usage", "allow", str(work / "beta"), "40") == 2
    assert Path(U.alloc_path()).read_text(encoding="utf-8") == before


def test_lift_needs_a_share(home, monkeypatch, work):
    at_terminal(monkeypatch, WORD + "\n")
    assert cli("usage", "lift", str(work / "alpha")) == 2


def test_a_lift_for_a_past_week_is_dropped_on_the_next_write(home, monkeypatch, work):
    os.makedirs(home)
    Path(U.alloc_path()).write_text('version = 1\n[[project]]\nfolder = %s\nshare = 50\n[[lift]]\nfolder = %s\n'
                                    'week = "2001-W01"\n' % ((json.dumps(str(work / "alpha")),) * 2),
                                    encoding="utf-8")
    at_terminal(monkeypatch, WORD + "\n")
    assert cli("usage", "budget", "100", "usd") == 0
    assert not U.load().data.get("lift")


def test_show_lists_shares_and_use(home, monkeypatch, work, capsys):
    os.makedirs(home)
    Path(U.alloc_path()).write_text('version = 1\n[budget]\nweekly = 1000\nunit = "tokens"\n[[project]]\n'
                                    'folder = %s\nshare = 50\n' % json.dumps(str(work / "alpha")), encoding="utf-8")
    transcript(work / "alpha", 450)
    transcript(work / "beta", 999)
    assert cli("usage", "show", "--json") == 0
    r = json.loads(capsys.readouterr().out)
    [p] = r["projects"]
    assert p["used"] == 450 and p["cap"] == 500 and p["state"] == "warn"
    assert cli("usage") == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "450 tokens of 500 tokens" in out and "warn" in out


def test_show_with_no_file(home, capsys):
    assert cli("usage") == 0
    assert "no project is limited" in capsys.readouterr().out
