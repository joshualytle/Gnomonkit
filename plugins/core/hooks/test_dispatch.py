#!/usr/bin/env python3
"""dispatch.py runs core's hooks in one process and must decide exactly as the standalone scripts do.

Self-contained: exits non-zero on failure (tests/test_plugin_hooks.py runs it with the rest).
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dispatch  # noqa: E402

FAILS = []


def check(cond, what):
    if not cond:
        FAILS.append(what)
        print("FAIL:", what)


def run(script, payload, env=None):
    e = dict(os.environ, **(env or {}))
    r = subprocess.run([sys.executable, os.path.join(HERE, script)], input=json.dumps(payload),
                       capture_output=True, text=True, env=e)
    return r.returncode, r.stdout, r.stderr


def bash(cmd, event="PreToolUse"):
    return {"hook_event_name": event, "tool_name": "Bash", "tool_input": {"command": cmd},
            "session_id": "s-dispatch-test", "cwd": HERE}


with tempfile.TemporaryDirectory() as tmp:
    env = {"CLAUDE_CONFIG_DIR": tmp, "GNOMONKIT_HOME": tmp, "HOME": tmp, "USERPROFILE": tmp}
    os.environ.update(env)

    # 1. A block from the dispatcher is the block the standalone hook gives (same exit code and message).
    bs = chr(92)
    heredoc = "cat <<EOF\nx " + bs + "n\nEOF"
    want = run("heredoc_guard.py", bash(heredoc))
    got = run("dispatch.py", bash(heredoc))
    check(want[0] == 2 and got[0] == 2, "heredoc block keeps exit 2 (%s/%s)" % (want[0], got[0]))
    check(want[2].strip() in got[2], "heredoc block message is passed on")

    # 2. An ordinary call passes with exit 0.
    check(run("dispatch.py", bash("echo hello"))[0] == 0, "plain command passes")

    # 3. The tool filter: the Bash-only guard does not see a Write; the interlock still blocks a shell wipe.
    seen = []
    orig = dispatch.run_one
    dispatch.run_one = lambda name, raw: (seen.append(name), (0, "", ""))[1]
    dispatch.HOOKS_SAVE = None
    try:
        raw = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Write",
                          "tool_input": {"file_path": "x.txt", "content": "y"}})
        sys.stdin = __import__("io").StringIO(raw)
        dispatch.main([])
    finally:
        sys.stdin = sys.__stdin__
        dispatch.run_one = orig
    check("heredoc_guard" not in seen and "destructive_interlock" not in seen, "shell guards skip a Write: %s" % seen)
    check({"grants_guard", "skill_router", "lessons_inject", "sync_guard"} <= set(seen), "write guards run: %s" % seen)

    # 4. Merge: contexts and messages join, a plain-text line survives, first value of other keys wins.
    merged = json.loads(dispatch.merge([
        json.dumps({"systemMessage": "a", "hookSpecificOutput": {"hookEventName": "X", "additionalContext": "one"}}),
        "",
        json.dumps({"systemMessage": "b", "hookSpecificOutput": {"hookEventName": "X", "additionalContext": "two"}}),
    ]))
    check(merged["systemMessage"] == "a\nb", "systemMessage joined")
    check(merged["hookSpecificOutput"]["additionalContext"] == "one\n\ntwo", "additionalContext joined")
    check(merged["hookSpecificOutput"]["hookEventName"] == "X", "event name kept once")
    check(dispatch.merge(["plain line"]) == "plain line", "plain text passes through")
    check(dispatch.merge(["", "  "]) == "", "nothing in, nothing out")

    # 5. Isolation: a hook that raises or calls sys.exit does not stop the next one.
    sys.path.insert(0, tmp)
    with open(os.path.join(tmp, "boom_hook.py"), "w", encoding="utf-8") as f:
        f.write("def main():\n    raise RuntimeError('boom')\n")
    with open(os.path.join(tmp, "exit_hook.py"), "w", encoding="utf-8") as f:
        f.write("import sys\ndef main():\n    print('hi')\n    sys.exit(2)\n")
    check(dispatch.run_one("boom_hook", "{}") == (0, "", ""), "an exception is swallowed")
    check(dispatch.run_one("exit_hook", "{}") == (2, "hi\n", ""), "sys.exit(2) is a block with its output")
    check(dispatch.run_one("no_such_hook_module", "{}")[0] == 0, "a missing module is skipped")
    saved = dispatch.HOOKS
    dispatch.HOOKS = {"PreToolUse": [("boom_hook", None), ("exit_hook", None), ("boom_hook", None)]}
    try:
        sys.stdin = __import__("io").StringIO(json.dumps(bash("echo hi")))
        check(dispatch.main([]) == 2, "a later block still lands after an earlier exception")
    finally:
        sys.stdin = sys.__stdin__
        dispatch.HOOKS = saved

    # 5b. A block is final: the hooks after it don't run, so a slow one can't time the block out.
    with open(os.path.join(tmp, "mark_hook.py"), "w", encoding="utf-8") as f:
        f.write("import os\ndef main():\n    open(os.environ['MARK_FILE'], 'w').close()\n    return 0\n")
    mark = os.path.join(tmp, "marked")
    os.environ["MARK_FILE"] = mark
    dispatch.HOOKS = {"PreToolUse": [("exit_hook", None), ("mark_hook", None)]}
    try:
        sys.stdin = __import__("io").StringIO(json.dumps(bash("echo hi")))
        check(dispatch.main([]) == 2, "the block lands")
        check(not os.path.exists(mark), "no hook runs after a block")
    finally:
        sys.stdin = sys.__stdin__
        dispatch.HOOKS = saved
        os.environ.pop("MARK_FILE", None)

    # 5c. Every blocking hook runs before any advisory one, in each event.
    BLOCKERS = {"heredoc_guard", "destructive_interlock", "grants_guard", "sync_guard", "usage_allocation"}
    for event, hooks in dispatch.HOOKS.items():
        names = [n for n, _ in hooks]
        first_advisory = next((i for i, n in enumerate(names) if n not in BLOCKERS), len(names))
        check(not any(n in BLOCKERS for n in names[first_advisory:]), "%s: blockers come first: %s" % (event, names))
    check("usage_allocation" in [n for n, _ in dispatch.HOOKS["UserPromptSubmit"]], "the usage hook is dispatched")

    # 6. Same output as the standalone skill_router on a trigger-matching call (the real rules, real merge).
    # Each run gets its own router state: the router shows a suggestion once per sitting, so a shared
    # state would leave the second run quiet. The probe must fire, or the comparison proves nothing.
    probe = bash("git push origin main")
    a = run("skill_router.py", probe, {"GNOMONKIT_ROUTER_STATE_DIR": os.path.join(tmp, "router-a")})
    b = run("dispatch.py", probe, {"GNOMONKIT_ROUTER_STATE_DIR": os.path.join(tmp, "router-b")})
    ctx_a = json.loads(a[1])["hookSpecificOutput"]["additionalContext"] if a[1].strip() else ""
    ctx_b = ""
    for line in b[1].splitlines():
        if line.startswith("{"):
            ctx_b = json.loads(line)["hookSpecificOutput"]["additionalContext"]
    check(a[0] == b[0] == 0, "router exit codes")
    check(ctx_a != "", "the router probe matches a rule")
    check(ctx_a in ctx_b, "router context is carried by the dispatcher")

# 7. hooks.json: each dispatcher entry's matcher is the union the table needs, and nothing else dispatches.
hj = json.load(open(os.path.join(HERE, "hooks.json"), encoding="utf-8"))["hooks"]
for event in dispatch.HOOKS:
    entries = [e for e in hj[event] if "dispatch.py" in e["hooks"][0]["command"]]
    check(len(entries) == 1, "%s has exactly one dispatcher entry" % event)
    if entries:
        check(entries[0].get("matcher", "") == dispatch.matcher(event), "%s matcher is the union" % event)
for event in ("UserPromptSubmit", "PreToolUse"):
    check(len(hj[event]) == 1, "%s is one process" % event)
for hook, _ in sum(dispatch.HOOKS.values(), []):
    check(os.path.exists(os.path.join(HERE, hook + ".py")), "hook script exists: " + hook)

if FAILS:
    sys.exit(1)
print("ok")
