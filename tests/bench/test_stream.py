"""Stream parser: tool calls, hook events in order, the result, and damaged streams."""
from streams import hook, init, result, text, tool_use, write_stream

from bench.stream import blocked, parse_stream, suggested_skills


def test_tools_hooks_and_result(tmp_path):
    p = write_stream(tmp_path, [init(), hook("PreToolUse:Write", ctx="  skill: package-manager-pinning\n    pin it"),
                                tool_use("Write", {"file_path": "C:\\r\\package.json"}), result("done", cost=0.2, turns=3)])
    t = parse_stream(p)
    assert [c.name for c in t.tools] == ["Write"]
    assert t.hooks[0].after_tool == 0 and suggested_skills(t.hooks[0]) == ["package-manager-pinning"]
    assert (t.result, t.cost, t.turns, t.error, t.truncated) == ("done", 0.2, 3, None, False)
    assert t.version == "2.1.303"


def test_both_hook_field_spellings(tmp_path):
    a = hook("PreToolUse:Bash", stdout="blocked", exit_code_key="exitCode", code=2)
    b = hook("PreToolUse:Bash", output="blocked", exit_code_key="exit_code", code=2)
    t = parse_stream(write_stream(tmp_path, [a, b]))
    assert [blocked(e) for e in t.hooks] == [True, True]


def test_truncated_last_line_kept_and_flagged(tmp_path):
    p = write_stream(tmp_path, [tool_use("Bash", {"command": "ls"})], tail='{"type": "assist')
    t = parse_stream(p)
    assert len(t.tools) == 1 and t.truncated and t.error == "truncated"


def test_no_result_event_is_an_error(tmp_path):
    t = parse_stream(write_stream(tmp_path, [tool_use("Bash", {"command": "ls"})]))
    assert t.error == "no-result" and not t.truncated


def test_hook_after_tool_counts_and_subagent_text(tmp_path):
    t = parse_stream(write_stream(tmp_path, [
        tool_use("Read", {"file_path": "a"}), tool_use("Write", {"file_path": "b"}, sub=True),
        hook("PostToolUse:Write", ctx="  skill: copy-discipline"), text("wrote b", sub=True), result()]))
    assert t.hooks[0].after_tool == 2 and t.hooks[0].event == "PostToolUse"
    assert [c.sub for c in t.tools] == [False, True] and t.sub_texts == ["wrote b"]


def test_plain_stdout_and_stderr_become_context(tmp_path):
    t = parse_stream(write_stream(tmp_path, [hook("PreToolUse:Bash", stdout="", stderr="BLOCKED: list it first", code=2)]))
    assert "BLOCKED" in t.hooks[0].context and blocked(t.hooks[0])


def test_missing_file_is_an_error_not_a_crash(tmp_path):
    t = parse_stream(tmp_path / "nope.out")
    assert t.error == "no-result" and t.tools == []
