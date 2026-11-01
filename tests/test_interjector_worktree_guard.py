"""The interjector's worktree guard: EnterWorktree is refused in a session that session-dispatch started, with
the `git worktree add` pattern instead, and left alone everywhere else.

Synthetic data only: a made-up Gnomonkit folder, session ids and transcripts under pytest's tmp_path.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "plugins" / "interjector" / "hooks" / "ij_worktree_guard.py"
SID = "5e551011-aaaa-4bbb-8ccc-0000000000a1"


@pytest.fixture
def run(tmp_path):
    home = tmp_path / "gk"
    (home / "dispatch").mkdir(parents=True)
    project = tmp_path / "projects" / "demo-repo"
    project.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("GNOMONKIT_", "CLAUDE_PLUGIN_OPTION_")) and k not in ("CLAUDECODE",)}
    env.update(GNOMONKIT_HOME=str(home), CLAUDE_CONFIG_DIR=str(tmp_path / "cfg"))

    def go(tool="EnterWorktree", first=None, record=False, role=None, payload=None):
        transcript = tmp_path / "t.jsonl"
        lines = [{"type": "user", "message": {"role": "user", "content": first}}] if first else []
        transcript.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
        (home / "dispatch" / "roles.json").write_text(
            json.dumps({"bg01": {"role": "sub", "sessionId": SID}} if record else {}), encoding="utf-8")
        e = dict(env, **({"GNOMONKIT_DISPATCH_ROLE": role} if role else {}))
        data = payload if payload is not None else json.dumps(
            {"tool_name": tool, "tool_input": {"name": "x"}, "session_id": SID, "cwd": str(project),
             "transcript_path": str(transcript), "hook_event_name": "PreToolUse"})
        r = subprocess.run([sys.executable, str(HOOK)], input=data, capture_output=True, text=True, env=e,
                           encoding="utf-8", timeout=60)
        out = r.stdout.strip()
        return r.returncode, (json.loads(out) if out else None)

    go.project = project
    return go


def denied(out):
    h = (out or {}).get("hookSpecificOutput") or {}
    return h.get("permissionDecision") == "deny" and h.get("permissionDecisionReason")


@pytest.mark.parametrize("how", [
    {"role": "sub"},
    {"record": True},
    {"first": "Your task brief is in /made/up/brief.md. Read it first and work from it."},
    {"first": "A new brief for this session is in /made/up/brief.md."},
])
def test_a_dispatched_session_is_refused_with_the_worktree_pattern(run, how):
    rc, out = run(**how)
    reason = denied(out)
    assert rc == 0 and reason
    assert "git -C" in reason and "worktree add" in reason and ".claude/worktrees/<name>" in reason
    assert "absolute path" in reason and "Never `cd`" in reason
    assert run.project.name in reason


def test_the_users_own_session_and_other_tools_are_left_alone(run):
    assert run(first="please tidy the readme") == (0, None)
    assert run() == (0, None)
    assert run(tool="Bash", role="sub") == (0, None)


def test_it_fails_open_on_bad_input(run):
    assert run(payload="not json") == (0, None)
    assert run(payload="[]") == (0, None)
