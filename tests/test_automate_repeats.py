"""automate-repeats detector: repeated command sequences, commands and requests across transcripts.

Synthetic transcripts only, in the shape Claude Code writes them, under a temp projects folder.
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins" / "core" / "skills" / "automate-repeats" / "detect_repeats.py"


def load():
    spec = importlib.util.spec_from_file_location("detect_repeats", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = load()


class T:
    """Builds one transcript: prompts, shell calls (with results) and MCP calls."""

    def __init__(self):
        self.lines = []
        self.n = 0

    def prompt(self, text):
        self.lines.append({"type": "user", "message": {"role": "user", "content": text}, "cwd": "/w/proj"})
        return self

    def tool(self, name, inp, result="ok"):
        self.n += 1
        tid = "toolu_%04d" % self.n
        self.lines.append({"type": "assistant", "message": {"id": "msg_%04d" % self.n, "role": "assistant",
                           "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}})
        self.lines.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "content": result}]}})
        return self

    def sh(self, cmd, result="ok", tool="Bash"):
        return self.tool(tool, {"command": cmd}, result)

    def write(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(x) for x in self.lines) + "\n", encoding="utf-8")


def ship(t, i):
    """One run of a made-up release process, with the incidental details varying per session."""
    return (t.sh('cd /w/proj && git status')
             .sh(f'python -m pytest -q tests/test_{i}.py', result="x" * 400)
             .sh(f'git add -A && git commit -m "change number {i}"')
             .sh(f'git push origin feature-{i}')
             .sh(f'gh pr create --title "Change {i}" --body-file /tmp/body{i}.md', result="https://example.invalid/pr/1")
             .tool("mcp__tracker__close_ticket", {"id": i}))


@pytest.fixture
def projects(tmp_path):
    root = tmp_path / "projects"
    for i in range(4):
        t = T().prompt(f"please ship the change for widget {i} and open the pull request")
        ship(t, i)
        t.prompt("thanks, keep going")
        t.sh("ls -la")
        t.write(root / ("proj-a" if i % 2 else "proj-b") / f"sess{i}.jsonl")
    # noise: a different process seen in one session only, repeated many times
    t = T().prompt("convert the image files to webp format for the gallery page")
    for j in range(6):
        t.sh(f"magick in{j}.png out{j}.webp").sh(f"cwebp -q 80 out{j}.webp").sh("optipng x.png")
    t.write(root / "proj-c" / "loner.jsonl")
    # a subagent transcript carrying the same process counts as its own session
    sub = T().prompt("Brief: ship it")
    ship(sub, 9)
    sub.write(root / "proj-a" / "sess1" / "subagents" / "agent-abc.jsonl")
    return root


def test_step_shapes():
    s = D.step_shape
    assert s('git commit -m "fix: thing; other" --amend') == "git commit"
    assert s("python -m pytest -q tests/x.py") == "python -m pytest"
    assert s('python "C:/somewhere/tools/leakcheck.py" --all') == "python leakcheck.py"
    assert s("FOO=1 BAR=2 npm run build") == "npm run build"
    assert s("timeout 60 npm test") == "npm test"
    assert s("do black $f") == "black"
    assert s("gh pr create --title x") == "gh pr create"
    assert s("ls -la") is None and s("git status") is None and s("done") is None and s("for f in *") is None
    assert s("ls -la", keep_inspection=True) == "ls"
    assert s("Get-ChildItem -Recurse") is None and s("Remove-Item -Recurse x") == "remove-item x"


def test_split_steps_keeps_quoted_separators_and_drops_heredoc_bodies():
    steps = D.split_steps('git commit -m "a && b; c" && python - <<\'EOF\'\nimport os\nprint(1)\nEOF\nnpm test')
    shapes = [D.step_shape(x) for x in steps]
    assert shapes == ["git commit", "python stdin", "npm test"]


def test_command_text_masks_what_varies():
    a = D.command_text('cd /w/p1 && ./build.sh --target "alpha" --jobs 4 deadbeef1234 https://example.invalid/x')
    b = D.command_text('cd /w/p2 && ./build.sh --target "beta" --jobs 8 cafebabe5678 https://example.invalid/y')
    assert a == b and "alpha" not in a
    tok = "gh" + "p_" + "A" * 30
    assert tok not in D.command_text(f"curl -H 'x' {tok} --fail https://example.invalid")


def test_sequences_ranked_across_sessions_not_within_one(projects):
    r = D.collect(projects, since_days=3650, workers=1)
    assert r["transcripts"] == 6 and r["main_sessions"] == 5
    top = r["sequences"][0]
    assert "git commit -> git push origin -> gh pr create" in top["pattern"]
    assert top["sessions"] == 5 and top["projects"] == 2         # four main sessions plus the subagent
    assert top["est_tokens_total"] > 0 and top["est_tokens_each"] > 0
    pats = " | ".join(x["pattern"] + " " + " ".join(x.get("variants", [])) for x in r["sequences"])
    assert "magick" not in pats                                   # one session's loop is not a recurring process
    assert "ls" not in pats.split() and "git status" not in pats
    assert any("mcp__tracker__close_ticket" in p for p in [pats])


def test_requests_grouped_by_shape_and_continuations_ignored(projects):
    r = D.collect(projects, since_days=3650, workers=1)
    [req] = r["requests"]
    assert req["sessions"] == 4 and req["occurrences"] == 4
    assert "ship" in req["pattern"] and "widget" in req["pattern"]
    assert all("keep going" not in x["pattern"] for x in r["requests"])
    assert req["est_tokens_each"] > 100                           # the turn's tool calls and results


def test_commands_need_min_sessions_and_skip_look_around(projects):
    r = D.collect(projects, since_days=3650, workers=1)
    pats = [c["pattern"] for c in r["commands"]]
    assert any(p.startswith("gh pr create") for p in pats)
    assert not any(p.startswith("cd ") and "git status" in p for p in pats)
    r2 = D.collect(projects, since_days=3650, min_sessions=6, workers=1)
    assert r2["sequences"] == [] and r2["commands"] == [] and r2["requests"] == []


def test_main_only_and_project_filter(projects):
    r = D.collect(projects, since_days=3650, main_only=True, workers=1)
    assert r["transcripts"] == 5 and r["sequences"][0]["sessions"] == 4
    r = D.collect(projects, since_days=3650, project_filter="proj-a", workers=1, min_sessions=2)
    assert r["transcripts"] == 3


def test_cli_prints_ranked_table_and_json(projects, tmp_path):
    out = tmp_path / "r.json"
    p = subprocess.run([sys.executable, str(SCRIPT), "--projects", str(projects), "--since", "3650",
                        "--json", str(out)], capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 0, p.stderr
    assert "Command sequences" in p.stdout and "MEASURED" in p.stdout and "ESTIMATED" in p.stdout
    assert json.loads(out.read_text(encoding="utf-8"))["sequences"]


def test_missing_root_is_empty_not_a_crash(tmp_path):
    r = D.collect(tmp_path / "nope", since_days=30, workers=1)
    assert r["transcripts"] == 0 and r["sequences"] == []


# ------------------------------------------------------------------ hostile input (core 0.28.0)
def shapes(cmd):
    return [x for x in (D.step_shape(s) for s in D.split_steps(cmd)) if x]


def test_heredocs_and_here_strings_drop_their_bodies():
    assert shapes("cat <<-END > out.txt\n\tgit push\n\tEND\nnpm test") == ["npm test"]
    assert shapes("python - <<A\nx = 1\nA\npython - <<'B'\nimport os\nB\nmake all") == \
        ["python stdin", "python stdin", "make all"]
    assert shapes("python - <<EOF\nEOF\nmake all") == ["python stdin", "make all"]      # an empty body
    assert "git push" in shapes("cat <<EOF\ngit push")                  # no end line: not a heredoc
    assert shapes("$x = @'\ngit push\n'@\nnpm test") == ["npm test"]
    assert shapes("@'x; @\"\ngit push\n\"@; npm test") == ["npm test"]  # an unclosed @' does not hide a later @"
    assert "git push" in shapes("$x = @'\ngit push")
    assert D.drop_heredocs("cat <<'EOF' x\nbody\nEOF\nnext") == "cat <<EOF x\nnext"


def test_prompt_words_drop_tagged_blocks():
    assert D.prompt_words("<note>alpha bravo</note> charlie delta") == ["charlie", "delta"]
    assert D.prompt_words("<note>alpha bravo") == ["note", "alpha", "bravo"]


def test_scans_of_long_synthetic_text_are_linear():
    """Synthetic worst cases: each took seconds or more before 0.28.0; now each is milliseconds."""
    n = 20_000
    cases = {
        "heredoc": (D.split_steps, "<<EOF x\n" * n),
        "heredoc quoted": (D.split_steps, "cat <<'EOF'\nline\n" * n),
        "here-string": (D.split_steps, "@'" * n),
        "here-strings mixed": (D.split_steps, "@'x\n@\"y\n" * n),
        "separators": (D.split_steps, " " * (5 * n) + "x"),
        "quotes": (D.split_steps, '"\\' * n),
        "secret names": (D.command_text, "KEY" * n),
        "tags": (D.prompt_words, "<a>" * n),
    }
    for name, (f, s) in cases.items():
        t0 = time.perf_counter()
        f(s)
        assert time.perf_counter() - t0 < 1.5, name


def test_inputs_are_capped():
    assert "make all" not in shapes("x" * (D.COMMAND_SCAN_MAX + 10) + "; make all")
    assert D.prompt_words("x " * D.PROMPT_SCAN_MAX + "zebra") == []
