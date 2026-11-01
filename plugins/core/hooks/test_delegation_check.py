#!/usr/bin/env python3
"""Fixtures for delegation_check: the checklist fires once per session; lookup-shaped general-purpose
briefs get the Explore suggestion. Every brief here is synthetic."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "delegation_check.py")
sys.path.insert(0, HERE)
import delegation_check as dc  # noqa: E402

FAILS = []


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def run(home, tool_input, session="synthetic-session", tool="Agent"):
    payload = {"hook_event_name": "PreToolUse", "session_id": session, "tool_name": tool,
               "tool_input": tool_input}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True,
                       env=dict(os.environ, GNOMONKIT_HOME=home))
    return r.returncode, r.stdout.strip()


def ctx(out):
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


LOOKUP = ["Find where the config loader reads its defaults and report the file and line.",
          "Which files import the widget module?",
          "Locate every call site of parse_header in the tree.",
          "List all the scripts under tools/ that take a --dry-run flag."]
WORK = ["Implement the retry option in the uploader and run the tests.",
        "Refactor the settings page into two components.",
        "Review this diff for correctness bugs."]

with tempfile.TemporaryDirectory() as home:
    code, out = run(home, {"subagent_type": "Explore", "prompt": "Find the router entry point."})
    check("first spawn gets the checklist", code == 0 and "delegation check" in ctx(out))
    check("checklist names the last-step rule", "last step" in ctx(out))
    check("Explore spawn gets no Explore suggestion", "Explore agent" not in ctx(out))
    data = json.loads(out)
    check("never blocks or decides", "permissionDecision" not in data["hookSpecificOutput"]
          and "decision" not in data)
    check("never edits the input", "updatedInput" not in data["hookSpecificOutput"])

    code, out = run(home, {"subagent_type": "Explore", "prompt": "Find the router entry point."})
    check("second spawn in the session is quiet", code == 0 and out == "")

    code, out = run(home, {"subagent_type": "general-purpose", "prompt": LOOKUP[0]})
    check("lookup-shaped general-purpose brief suggests Explore",
          "Explore" in ctx(out) and "delegation check" not in ctx(out))

    code, out = run(home, {"subagent_type": "general-purpose", "prompt": WORK[0]})
    check("work brief later in the session is quiet", code == 0 and out == "")

    code, out = run(home, {"prompt": LOOKUP[1]}, tool="Task")
    check("Task tool name and a missing type count as general-purpose", "Explore" in ctx(out))

    code, out = run(home, {"subagent_type": "Explore"}, session="second-session")
    check("a new session gets the checklist again", "delegation check" in ctx(out))

    code, out = run(home, {"prompt": LOOKUP[0]}, tool="Bash", session="third-session")
    check("other tools are ignored", code == 0 and out == "")

    r = subprocess.run([sys.executable, HOOK], input="garbage {", capture_output=True, text=True,
                       env=dict(os.environ, GNOMONKIT_HOME=home))
    check("garbage payload exits 0 silently", r.returncode == 0 and r.stdout.strip() == "")

for p in LOOKUP:
    check("lookup: " + p[:40], dc.reads_like_lookup(p))
for p in WORK:
    check("not lookup: " + p[:40], not dc.reads_like_lookup(p))

# Calibration for the cheaper-model suggestion: known positives and known negatives, all synthetic.
SMALL = ["Summarise the README of each package under libs/ in one line apiece and report back.",
         "Gather the version strings from the three manifest files and return a short summary.",
         "Check whether the log file mentions a timeout, and tell me what line.",
         "Count the TODO markers per directory under src/ and return the list.",
         "Find where the config loader reads its defaults."]
BIG = ["Design the caching layer for the importer and write the plan.",
       "Debug why the nightly job times out; find the root cause.",
       "Implement the retry option in the uploader and run the tests.",
       "Review this diff for correctness bugs.",
       "Refactor the settings page into two components.",
       "Summarise the history of this module " + "and its callers " * 100,
       "Please help with the thing."]
for p in SMALL:
    check("small job: " + p[:40], dc.small_job(p))
for p in BIG:
    check("not small: " + p[:40], not dc.small_job(p))

with tempfile.TemporaryDirectory() as home:
    code, out = run(home, {"subagent_type": "general-purpose", "prompt": SMALL[0]}, session="m1")
    check("untyped small job with no model gets the cheaper hint", "collector" in ctx(out))
    code, out = run(home, {"prompt": SMALL[1]}, session="m2")
    check("a missing type counts as general-purpose", "collector" in ctx(out))
    code, out = run(home, {"subagent_type": "general-purpose", "model": "haiku", "prompt": SMALL[0]},
                    session="m3")
    check("a model that is set gets no cheaper hint", "collector" not in ctx(out))
    code, out = run(home, {"subagent_type": "collector", "prompt": SMALL[0]}, session="m4")
    check("a typed agent gets no cheaper hint", "collector" not in ctx(out))
    code, out = run(home, {"subagent_type": "general-purpose", "prompt": BIG[0]}, session="m5")
    check("a big job gets no cheaper hint", "collector" not in ctx(out))
    check("the hint never blocks", "permissionDecision" not in json.loads(
        run(home, {"prompt": SMALL[2]}, session="m6")[1])["hookSpecificOutput"])

print("FAILED: %d" % len(FAILS) if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
