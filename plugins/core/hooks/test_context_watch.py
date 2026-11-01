#!/usr/bin/env python3
"""Fixtures for context_watch: synthetic transcripts only, written fresh into a temp folder.

Every number and line here is made up to exercise one branch each: crossing the budget, re-notify
at +100k, staying quiet below it, off at 0, garbage or missing transcripts, and the tail read; then
the compaction count: quiet at one, speaking at max_compactions and once per further count, off at 0,
subagent compactions ignored, and the incremental scan from a saved offset."""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "context_watch.py")
sys.path.insert(0, HERE)
import context_watch as cw  # noqa: E402

FAILS = []


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def turn(inp, read, create, sidechain=False):
    return json.dumps({"type": "assistant", "isSidechain": sidechain,
                       "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}],
                                   "usage": {"input_tokens": inp, "cache_read_input_tokens": read,
                                             "cache_creation_input_tokens": create,
                                             "output_tokens": 10}}})


def write_transcript(path, lines):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def clean_env(home, **extra):
    """No plugin option, GNOMONKIT_* setting or personal profile from the developer's own setup."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_PLUGIN_OPTION_", "GNOMONKIT_"))}
    env["GNOMONKIT_HOME"] = home
    env.update(extra)
    return env


def run(home, transcript, session="synthetic-session", budget=None):
    env = clean_env(home)
    if budget is not None:
        env["CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET"] = str(budget)
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": session, "prompt": "next step",
               "transcript_path": transcript}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                       text=True, env=env)
    return r.returncode, r.stdout.strip()


with tempfile.TemporaryDirectory() as root:
    home = os.path.join(root, "gk")
    tr = os.path.join(root, "t.jsonl")
    user = json.dumps({"type": "user", "message": {"role": "user", "content": "go"}})

    write_transcript(tr, [user, turn(5, 120_000, 2_000)])
    code, out = run(home, tr)
    check("below budget stays quiet", code == 0 and out == "")

    write_transcript(tr, [user, turn(5, 120_000, 2_000), user, turn(10, 250_000, 5_000)])
    code, out = run(home, tr)
    check("crossing the budget speaks", code == 0 and out != "")
    if out:
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        check("names the context and budget", "Context is at 255k tokens (budget 250k)" in ctx)
        check("tells the user too", "systemMessage" in data and "/compact" in data["systemMessage"])
        check("says to write the handoff first", "write the handoff" in ctx)
        check("tells it to finish the step and offers a fresh session", "Finish the current step" in ctx
              and "session-dispatch handoff" in ctx and "/compact" in ctx)

    write_transcript(tr, [user, turn(10, 300_000, 5_000)])
    code, out = run(home, tr)
    check("same 100k band stays quiet", code == 0 and out == "")

    write_transcript(tr, [user, turn(10, 352_000, 0)])
    code, out = run(home, tr)
    check("re-notifies at +100k", code == 0 and "352k" in out)

    write_transcript(tr, [user, turn(10, 60_000, 0)])
    code, out = run(home, tr)
    check("after compact, quiet", code == 0 and out == "")
    write_transcript(tr, [user, turn(10, 260_000, 0)])
    code, out = run(home, tr)
    check("after compact, the next crossing speaks again", code == 0 and "260k" in out)

    code, out = run(home, tr, session="other-session")
    check("state is per session", code == 0 and "260k" in out)

    code, out = run(os.path.join(root, "gk-off"), tr, budget=0)
    check("budget 0 disables", code == 0 and out == "")

    code, out = run(os.path.join(root, "gk-low"), tr, budget="100000")
    check("budget option is read", code == 0 and "budget 100k" in out)

    # Precedence: the plugin option, then the personal profile (~/.gnomonkit/personal.toml), then 250k.
    ph = os.path.join(root, "gk-profile")
    os.makedirs(ph)
    with open(os.path.join(ph, "personal.toml"), "w", encoding="utf-8") as fh:
        fh.write("[context]\ncontext_budget = 200000\n")
    code, out = run(ph, tr)
    check("the profile's budget applies when no option is set", code == 0 and "budget 200k" in out)
    code, out = run(os.path.join(root, "gk-profile-opt"), tr, budget="270000")
    check("no profile, option above the context: quiet", code == 0 and out == "")
    os.makedirs(os.path.join(root, "gk-profile-opt2"))
    with open(os.path.join(root, "gk-profile-opt2", "personal.toml"), "w", encoding="utf-8") as fh:
        fh.write("[context]\ncontext_budget = 200000\n")
    code, out = run(os.path.join(root, "gk-profile-opt2"), tr, budget="270000")
    check("the option wins over the profile", code == 0 and out == "")
    os.makedirs(os.path.join(root, "gk-profile-bad"))
    with open(os.path.join(root, "gk-profile-bad", "personal.toml"), "w", encoding="utf-8") as fh:
        fh.write('[context]\ncontext_budget = "lots"\n')
    code, out = run(os.path.join(root, "gk-profile-bad"), tr)
    check("an invalid profile value falls back to the default", code == 0 and "budget 250k" in out)

    write_transcript(tr, [user, turn(10, 900_000, 0, sidechain=True), turn(1, 1_000, 0)])
    code, out = run(os.path.join(root, "gk-side"), tr)
    check("subagent (sidechain) turns are ignored", code == 0 and out == "")

    code, out = run(home, os.path.join(root, "missing.jsonl"))
    check("missing transcript exits 0 silently", code == 0 and out == "")

    with open(tr, "wb") as fh:
        fh.write(b"\x00\xff not json at all {{{\n" * 50)
    code, out = run(home, tr)
    check("garbage transcript exits 0 silently", code == 0 and out == "")

    env = clean_env(home)
    r = subprocess.run([sys.executable, HOOK], input="not json", capture_output=True, text=True, env=env)
    check("garbage payload exits 0 silently", r.returncode == 0 and r.stdout.strip() == "")

    # Tail read: a large transcript whose head is unreadable to json; only the tail may be parsed.
    big = os.path.join(root, "big.jsonl")
    filler = ("{" + '"type": "user", "pad": "' + "x" * 2000 + '"}\n').encode()
    with open(big, "wb") as fh:
        fh.write(b"{broken head line that is not json\n")
        for _ in range(3_000):  # ~6 MB of synthetic filler
            fh.write(filler)
        fh.write((turn(10, 270_000, 0) + "\n").encode())
    t0 = time.perf_counter()
    ctx = cw.last_context(big)
    took = time.perf_counter() - t0
    check("tail read finds the last turn in a large file", ctx == 270_010)
    check("tail read is fast (no whole-file read)", took < 0.15)

    # A last turn longer than the first tail still parses from the larger tail.
    long_turn = json.loads(turn(10, 280_000, 0))
    long_turn["message"]["content"][0]["text"] = "y" * (200 * 1024)
    with open(big, "ab") as fh:
        fh.write((json.dumps(long_turn) + "\n").encode())
    check("a long last line is still found", cw.last_context(big) == 280_010)

    # --- compaction count (synthetic boundary and summary entries) ---
    def boundary(sidechain=False, trigger="auto"):
        return json.dumps({"type": "system", "subtype": "compact_boundary", "isSidechain": sidechain,
                           "content": "Conversation compacted", "compactMetadata": {"trigger": trigger}})

    def summary():
        return json.dumps({"type": "user", "isCompactSummary": True,
                           "message": {"role": "user", "content": "summary of earlier work"}})

    def run_c(home_dir, transcript, cap=None, budget_=None, session="compact-session", manual=None):
        env = clean_env(home_dir)
        if cap is not None:
            env["CLAUDE_PLUGIN_OPTION_MAX_COMPACTIONS"] = str(cap)
        if manual is not None:
            env["CLAUDE_PLUGIN_OPTION_MANUAL_COMPACTIONS"] = str(manual)
        if budget_ is not None:
            env["GNOMONKIT_CONTEXT_BUDGET"] = str(budget_)
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": session, "prompt": "next",
                   "transcript_path": transcript}
        r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True,
                           text=True, env=env)
        return r.returncode, r.stdout.strip()

    ch = os.path.join(root, "gk-compact")
    ct = os.path.join(root, "compact.jsonl")
    small = turn(5, 40_000, 0)
    write_transcript(ct, [user, small])
    code, out = run_c(ch, ct)
    check("no compaction: quiet", code == 0 and out == "")

    write_transcript(ct, [user, small, boundary(), summary(), user, small])
    code, out = run_c(ch, ct)
    check("one compaction (boundary + summary counted once): quiet", code == 0 and out == "")

    with open(ct, "a", encoding="utf-8") as fh:
        fh.write("\n".join([boundary(), summary(), user, small]) + "\n")
    code, out = run_c(ch, ct)
    check("second compaction reaches the default cap and speaks", code == 0 and out != "")
    if out:
        data = json.loads(out)
        ctx = data["hookSpecificOutput"]["additionalContext"]
        check("agent is told to write the handoff via session-close",
              "compacted 2 times" in ctx and "core:session-close" in ctx)
        sm = data.get("systemMessage", "")
        check("user is told the manual /compact left, or a fresh session",
              "compacted 2 times" in sm and "/compact (1 manual" in sm and "fresh session" in sm
              and "forgetting" in sm)
    code, out = run_c(ch, ct)
    check("same count speaks only once", code == 0 and out == "")

    with open(ct, "a", encoding="utf-8") as fh:
        fh.write("\n".join([boundary(), summary(), user, small]) + "\n")
    code, out = run_c(ch, ct)
    check("third compaction speaks again", code == 0 and "compacted 3 times" in out)
    st_file = os.path.join(ch, "context-watch", "compact-session.json")
    st = json.load(open(st_file, encoding="utf-8"))
    check("state keeps a byte offset (incremental scan)", st.get("offset") == os.path.getsize(ct))

    code, out = run_c(os.path.join(root, "gk-c-off"), ct, cap=0)
    check("max_compactions 0 turns the count off", code == 0 and out == "")
    code, out = run_c(os.path.join(root, "gk-c-3"), ct, cap=3)
    check("max_compactions option is read", code == 0 and "compacted 3 times" in out)
    code, out = run_c(os.path.join(root, "gk-c-4"), ct, cap=4)
    check("under a higher cap: quiet", code == 0 and out == "")
    os.makedirs(os.path.join(root, "gk-c-profile"))
    with open(os.path.join(root, "gk-c-profile", "personal.toml"), "w", encoding="utf-8") as fh:
        fh.write("[context]\nmax_compactions = 3\n")
    code, out = run_c(os.path.join(root, "gk-c-profile"), ct)
    check("the profile's max_compactions applies when no option is set", code == 0 and "compacted 3 times" in out)
    env_alt = os.path.join(root, "gk-c-env")
    e = clean_env(env_alt, GNOMONKIT_MAX_COMPACTIONS="1")
    r = subprocess.run([sys.executable, HOOK], capture_output=True, text=True, env=e,
                       input=json.dumps({"session_id": "s", "transcript_path": ct}))
    check("GNOMONKIT_MAX_COMPACTIONS is read too", r.returncode == 0 and "compacted 3 times" in r.stdout)

    code, out = run_c(os.path.join(root, "gk-c-m0"), ct, manual=0)
    check("no manual allowance: a fresh session is recommended",
          "compacted 3 times" in out and "/compact" not in out and "fresh session" in out)

    # Manual compactions count against their own allowance, not against the automatic cap.
    mt = os.path.join(root, "manual.jsonl")
    write_transcript(mt, [user, boundary(), boundary(trigger="manual"), small])
    code, out = run_c(os.path.join(root, "gk-c-man1"), mt)
    check("one automatic and one manual: quiet", code == 0 and out == "")
    mh = os.path.join(root, "gk-c-man2")
    write_transcript(mt, [user, boundary(), boundary(), small])
    code, out = run_c(mh, mt)
    check("two automatic: the manual one is still offered", "/compact (1 manual" in out)
    with open(mt, "a", encoding="utf-8") as fh:
        fh.write("\n".join([boundary(trigger="manual"), small]) + "\n")
    code, out = run_c(mh, mt)
    check("after the manual one: a fresh session is recommended",
          "compacted 3 times" in out and "/compact" not in out and "fresh session" in out)
    write_transcript(mt, [user, boundary(trigger="manual"), boundary(trigger="manual"), small])
    code, out = run_c(os.path.join(root, "gk-c-man3"), mt)
    check("manual compactions past the allowance speak", "compacted 2 times" in out and "/compact" not in out)

    code, out = run_c(os.path.join(root, "gk-c-b0"), ct, budget_=0)
    check("budget 0 leaves the compaction count on", code == 0 and "compacted 3 times" in out)

    side = os.path.join(root, "side.jsonl")
    write_transcript(side, [user, boundary(sidechain=True), boundary(sidechain=True), small])
    code, out = run_c(os.path.join(root, "gk-c-side"), side)
    check("subagent compactions are not counted", code == 0 and out == "")

    only_sum = os.path.join(root, "summaries.jsonl")
    write_transcript(only_sum, [user, summary(), small, summary(), small])
    code, out = run_c(os.path.join(root, "gk-c-sum"), only_sum)
    check("summaries alone are counted when there are no boundaries",
          code == 0 and "compacted 2 times" in out)

    both = os.path.join(root, "both.jsonl")
    write_transcript(both, [user, turn(10, 60_000, 0)])
    bh = os.path.join(root, "gk-c-both")
    run_c(bh, both)
    with open(both, "a", encoding="utf-8") as fh:  # transcripts only grow
        fh.write("\n".join([boundary(), small, boundary(), turn(10, 255_000, 0)]) + "\n")
    code, out = run_c(bh, both)
    check("budget and compaction speak in one message",
          code == 0 and "255k" in out and "compacted 2 times" in out)

    # A replaced (shorter) transcript restarts the count instead of trusting a stale offset.
    rh = os.path.join(root, "gk-c-reset")
    write_transcript(ct, [user, boundary(), boundary(), small] + [user] * 50)
    run_c(rh, ct)
    write_transcript(ct, [user, small])
    code, out = run_c(rh, ct)
    check("a shorter transcript resets: quiet", code == 0 and out == "")
    with open(ct, "a", encoding="utf-8") as fh:
        fh.write("\n".join([boundary(), boundary(), small]) + "\n")
    code, out = run_c(rh, ct)
    check("after a reset, the cap speaks again", code == 0 and "compacted 2 times" in out)

    # A line still being written (no newline yet) is not counted until it is complete.
    part = os.path.join(root, "partial.jsonl")
    with open(part, "w", encoding="utf-8") as fh:
        fh.write(user + "\n" + boundary() + "\n" + boundary())
    off, b, s, m = cw.scan_compactions(part)
    check("an incomplete last line waits", b == 1 and off < os.path.getsize(part))
    with open(part, "a", encoding="utf-8") as fh:
        fh.write("\n")
    off, b, s, m = cw.scan_compactions(part, off, b, s, m)
    check("and is counted once complete", b == 2 and off == os.path.getsize(part))

    # Incremental: a large transcript with no markers is scanned quickly, and the next scan reads nothing.
    t0 = time.perf_counter()
    off, b, s, m = cw.scan_compactions(big)
    took = time.perf_counter() - t0
    check("a marker-free 6 MB transcript scans fast", b == 0 and s == 0 and took < 0.5)
    check("a rescan from the saved offset reads nothing new", cw.scan_compactions(big, off) == (off, 0, 0, 0))

print("FAILED: %d" % len(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
