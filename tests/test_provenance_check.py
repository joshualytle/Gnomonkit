"""Tests for scripts/provenance_check.py. Every sentence that should fire is assembled from fragments at
runtime, so this file passes the check itself."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "provenance_check.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import provenance_check as pc  # noqa: E402

J = " ".join
FIRE = {
    "count": [J(["Replayed", "4,210", "typed", "prompts"]), J(["across", "350", "sessions"]),
              J(["it", "found", "812", "subagent", "spawns"]), J(["on", "7", "real", "sessions"])],
    "whose": [J(["read", "one", "user's", "transcripts"]), J(["from", "the", "owner's", "prompts"]),
              J(["in", "our", "sessions"])],
    "replay": [J(["Replayed", "every", "command", "in", "the", "local", "transcripts"]),
               J(["Measured", "on", "40", "real", "requests"])],
    "realrun": [J(["A", "real", "run", "over", "the", "disk", "found", "12", "items", "(3.1", "GB)"])],
    "machine": [J(["counts", "taken", "on", "one", "workstation"]), J(["## Real", "run", "on", "this", "machine"])],
    "drive": ["scratch went to " + "X" + ":\\" + "scratch\\qa"],
}
QUIET = [
    "help: run on this machine with no sandbox",
    "| Conversation has passed ~20 messages | re-read cost |",
    "Synthetic fixtures prove the mechanism; the owner's data proves the numbers.",
    "Full suite: 214 passed; leakcheck clean.",
    "Real network runs against public packages: left-pad 1.2.0..1.3.0 flagged medium.",
    "Paths start at C:\\Users\\<you>\\project or /home/<you>/project.",
    "Run it from your own transcripts to calibrate it for your setup.",
]


@pytest.mark.parametrize("rule,text", [(r, t) for r, ts in FIRE.items() for t in ts])
def test_fires(rule, text):
    hits = list(pc.scan([("x", 1, text)]))
    assert hits and hits[0][2] == rule, (rule, text, hits)


@pytest.mark.parametrize("text", QUIET)
def test_quiet(text):
    assert list(pc.scan([("x", 1, text)])) == [], text


def test_own_source_and_tests_are_exempt():
    assert list(pc.scan([("scripts/provenance_check.py", 1, FIRE["count"][0]),
                         ("tests\\test_provenance_check.py", 2, FIRE["whose"][0])])) == []


def test_marker_exempts_a_generic_line():
    text = FIRE["count"][0] + "  <!-- provenance-" + "ok: a made-up example in the docs -->"
    assert list(pc.scan([("x", 1, text)])) == []


def test_added_lines_only_and_line_numbers():
    diff = ("diff --git a/f.md b/f.md\n--- a/f.md\n+++ b/f.md\n@@ -3,0 +4,2 @@\n+plain line\n+"
            + FIRE["whose"][0] + "\n@@ -9 +11 @@\n-" + FIRE["count"][1] + "\n+still fine\n")
    assert list(pc.scan(pc.added_lines(diff))) == [("f.md", 5, "whose")]


def run(args, cwd, stdin=None):
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=cwd, input=stdin, capture_output=True,
                          text=True, encoding="utf-8")


def test_stdin_reports_location_never_text():
    r = run(["--stdin", "pr-body"], ROOT, stdin="intro\n" + FIRE["count"][0] + "\n")
    assert r.returncode == 1
    assert "pr-body:2: owner-evidence/count" in r.stdout
    assert "typed" not in r.stdout + r.stderr


def test_diff_mode_on_a_real_repo(tmp_path):
    def git(*a):
        subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True, text=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t" + "@" + "example.invalid")
    git("config", "user.name", "t")
    (tmp_path / "old.md").write_text(FIRE["count"][1] + "\n", encoding="utf-8")   # predates the check
    git("add", "-A")
    git("commit", "-qm", "base")
    (tmp_path / "new.md").write_text("ok\n" + FIRE["replay"][0] + "\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "head")
    r = run(["--diff", "HEAD~1..HEAD"], str(tmp_path))
    assert r.returncode == 1 and "new.md:2: owner-evidence/replay" in r.stdout and "old.md" not in r.stdout
    git("commit", "-q", "--allow-empty", "-m", "noop")
    assert run(["--diff", "HEAD~1..HEAD"], str(tmp_path)).returncode == 0
