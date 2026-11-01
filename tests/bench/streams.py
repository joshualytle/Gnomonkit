"""Builders for synthetic stream-json lines, shaped like a real `claude -p --output-format stream-json
--verbose --include-hook-events` run (shapes observed on Claude Code 2.1.x). Tests write these to a temp
file; no stream file is ever committed (the leak gates block .jsonl)."""
import json


def init(version="2.1.303"):
    return {"type": "system", "subtype": "init", "claude_code_version": version, "model": "test-model"}


def hook(name, ctx=None, output=None, stdout=None, stderr="", code=0, exit_code_key="exit_code"):
    """A hook_response. `ctx` becomes hookSpecificOutput.additionalContext in the JSON output."""
    if ctx is not None:
        body = json.dumps({"hookSpecificOutput": {"hookEventName": name.split(":")[0], "additionalContext": ctx}})
        output = stdout = body
    ev = {"type": "system", "subtype": "hook_response", "hook_name": name, "hook_event": name.split(":")[0],
          "stderr": stderr, "outcome": "success" if code == 0 else "error"}
    if output is not None:
        ev["output"] = output
    if stdout is not None:
        ev["stdout"] = stdout
    ev[exit_code_key] = str(code)          # real streams carry the exit code as a string
    return ev


def tool_use(name, inp, sub=False):
    return {"type": "assistant", "parent_tool_use_id": "toolu_parent" if sub else None,
            "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}


def text(t, sub=False):
    return {"type": "assistant", "parent_tool_use_id": "toolu_parent" if sub else None,
            "message": {"content": [{"type": "text", "text": t}]}}


def result(r="done", cost=0.1, turns=3, is_error=False, subtype="success"):
    return {"type": "result", "subtype": subtype, "result": r, "total_cost_usd": cost, "num_turns": turns,
            "is_error": is_error}


def make_repo(tmp_path, files, name="repo"):
    """A scenario repo as a run would leave it (no git needed for scoring)."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


def trace_of(tmp_path, *events, name="t.out"):
    from bench.stream import parse_stream
    return parse_stream(write_stream(tmp_path, list(events) + [result()], name=name))


def write_stream(tmp_path, events, tail=None, name="stream.out"):
    p = tmp_path / name
    lines = [json.dumps(e) for e in events]
    if tail is not None:
        lines.append(tail)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
