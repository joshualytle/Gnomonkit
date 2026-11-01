#!/usr/bin/env python3
"""Fixtures for compact_carryover: synthetic transcripts only, written fresh into a temp folder.

Every id, repo and path here is made up. Each block exercises one branch: PR links and URLs, a
background task and a subagent that finished against ones still running, edited files, the handoff
file, worktrees, the last user message, unanswered questions, the compaction boundary, carrying
running items over from the previous record, the SessionStart injection and its cap, and failing
silently on bad input."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "compact_carryover.py")
sys.path.insert(0, HERE)
import compact_carryover as cc  # noqa: E402

FAILS = []
REPO = "example-org" + "/" + "widget"
SEP = os.sep


def check(name, cond):
    print(("  ok    " if cond else "  FAIL  ") + name)
    if not cond:
        FAILS.append(name)


def user(text, cwd="/work/proj", meta=False):
    e = {"type": "user", "cwd": cwd, "message": {"role": "user", "content": text}}
    if meta:
        e["isMeta"] = True
    return e


def assistant(*blocks, sidechain=False, cwd="/work/proj"):
    return {"type": "assistant", "isSidechain": sidechain, "cwd": cwd,
            "message": {"role": "assistant", "content": list(blocks)}}


def text(t):
    return {"type": "text", "text": t}


def tool_use(tid, name, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def result(tid, out, extra=None, cwd="/work/proj"):
    e = {"type": "user", "cwd": cwd,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                   "content": [{"type": "text", "text": out}]}]}}
    if extra is not None:
        e["toolUseResult"] = extra
    return e


def notification(task_id, status="completed"):
    return user("<task-notification>\n<task-id>%s</task-id>\n<status>%s</status>\n"
                "<summary>synthetic</summary>\n</task-notification>" % (task_id, status))


def write(path, entries, garbage=False):
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
        if garbage:
            fh.write("{not json\n")


def run(home, payload, env_extra=None):
    env = dict(os.environ, GNOMONKIT_HOME=home)
    env.pop("GNOMONKIT_CARRYOVER", None)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, HOOK], input=payload if isinstance(payload, str) else json.dumps(payload),
                       capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.strip()


def precompact(home, tr, session="synthetic-session", cwd="/work/proj"):
    return run(home, {"hook_event_name": "PreCompact", "session_id": session, "transcript_path": tr,
                      "cwd": cwd, "trigger": "auto", "custom_instructions": None})


def start(home, session="synthetic-session", source="compact", cwd="/work/proj"):
    code, out = run(home, {"hook_event_name": "SessionStart", "session_id": session, "source": source,
                           "cwd": cwd, "transcript_path": "unused"})
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""
    return code, out, ctx


with tempfile.TemporaryDirectory() as root:
    home = os.path.join(root, "gk")
    os.environ["GNOMONKIT_HOME"] = home  # so cc.record_path() here points where the hook writes
    tr =os.path.join(root, "t.jsonl")
    handoff = os.path.join(root, "HANDOFF.md")
    with open(handoff, "w", encoding="utf-8") as fh:
        fh.write("synthetic handoff\n")
    wt_file = SEP.join([root, ".claude", "worktrees", "agent-x1", "src", "app.py"])
    pr_url = "https://github.com/%s/pull/41" % REPO
    issue_url = "https://github.com/%s/issues/7" % REPO

    entries = [
        # before the boundary: must be dropped
        user("old request about PR 999"),
        assistant(tool_use("t0", "Write", file_path="/work/proj/old.txt", content="x")),
        {"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"},
        user("please open the PR and start the slow test run"),
        assistant(tool_use("t1", "Bash", command="gh pr create --fill", description="Open the PR")),
        result("t1", pr_url + "\n"),
        {"type": "pr-link", "prNumber": 41, "prUrl": pr_url, "prRepository": REPO},
        assistant(text("Tracking %s as well." % issue_url),
                  tool_use("t2", "Bash", command="sleep 600", description="Slow test run", run_in_background=True)),
        result("t2", "Command running in background with ID: bsynth01", {"backgroundTaskId": "bsynth01"}),
        assistant(tool_use("t3", "Bash", command="sleep 1", description="Quick wait", run_in_background=True)),
        result("t3", "Command running in background with ID: bsynth02", {"backgroundTaskId": "bsynth02"}),
        notification("bsynth02"),
        assistant(tool_use("t4", "Agent", description="Survey the parser", subagent_type="Explore",
                           prompt="look", run_in_background=True)),
        result("t4", "Async agent launched", {"status": "async_launched", "agentId": "asynthagent01",
                                              "description": "Survey the parser"}),
        assistant(tool_use("t5", "Agent", description="Check the docs", prompt="look", run_in_background=True)),
        result("t5", "Async agent launched", {"status": "async_launched", "agentId": "asynthagent02",
                                              "description": "Check the docs"}),
        {"type": "queue-operation", "operation": "enqueue",
         "content": "<task-notification>\n<task-id>asynthagent02</task-id>\n<status>completed</status>\n"
                    "</task-notification>"},
        assistant(tool_use("t6", "Edit", file_path=wt_file, old_string="a", new_string="b")),
        result("t6", "ok"),
        assistant(tool_use("t7", "Write", file_path=handoff, content="state")),
        result("t7", "ok"),
        assistant(tool_use("t8", "Bash", command="git worktree add ../feature-wt -b feature")),
        result("t8", "Preparing worktree"),
        user("<system-reminder>injected</system-reminder>", meta=True),
        assistant(text("noise from a subagent?"), sidechain=True),
        assistant(text("The PR is open. Should I also bump the version now?")),
    ]
    write(tr, entries, garbage=True)

    code, out = precompact(home, tr)
    rec_path = cc.record_path("synthetic-session")
    check("precompact exits 0 and prints nothing", code == 0 and out == "")
    check("writes the record under GNOMONKIT_HOME/carryover", os.path.isfile(rec_path))
    rec = json.load(open(rec_path, encoding="utf-8")) if os.path.isfile(rec_path) else {}
    prs = {(p["kind"], p["number"]): p for p in rec.get("prs", [])}
    check("PR from pr-link and gh pr create, marked created",
          prs.get(("pr", 41), {}).get("source") == "created" and prs[("pr", 41)]["repo"] == REPO)
    check("issue URL in assistant text is referenced", prs.get(("issue", 7), {}).get("source") == "referenced")
    check("items before the compaction boundary are dropped",
          ("pr", 999) not in prs and "/work/proj/old.txt" not in rec.get("files", []))
    check("running background task kept, finished one dropped",
          [t["id"] for t in rec.get("background_tasks", [])] == ["bsynth01"])
    check("background task keeps its description", rec["background_tasks"][0]["description"] == "Slow test run")
    check("running subagent kept, notified one dropped (queue-operation)",
          [a["id"] for a in rec.get("agents", [])] == ["asynthagent01"])
    check("subagent type recorded", rec["agents"][0]["type"] == "Explore")
    check("edited files, most recent first", rec.get("files", [])[:2] == [handoff, wt_file])
    check("handoff file found and stat'ed", (rec.get("handoff") or {}).get("path") == handoff
          and rec["handoff"]["exists"] is True)
    wts = rec.get("worktrees", [])
    check("worktree from an edited path", SEP.join([root, ".claude", "worktrees", "agent-x1"]) in wts)
    check("worktree from git worktree add", "../feature-wt" in wts)
    check("last user message is the typed one, not the injected one",
          rec.get("last_user_message") == "please open the PR and start the slow test run")
    check("unanswered question captured, sidechain ignored",
          rec.get("pending_questions") == ["Should I also bump the version now?"])
    check("session and trigger stored", rec.get("session_id") == "synthetic-session" and rec.get("trigger") == "auto")

    code, out, ctx = start(home)
    check("SessionStart(compact) injects additionalContext", code == 0 and ctx.startswith("Carried over from before compaction"))
    check("tells the model to verify", "verify each item before relying on it" in ctx)
    check("names the running task, agent, PR and handoff",
          "bsynth01" in ctx and "asynthagent01" in ctx and "PR #41 " + REPO in ctx and handoff in ctx)
    check("finished items are not injected", "bsynth02" not in ctx and "asynthagent02" not in ctx)
    check("hookEventName is SessionStart", json.loads(out)["hookSpecificOutput"]["hookEventName"] == "SessionStart")

    code, out, _ = start(home, source="startup")
    check("SessionStart other than compact stays quiet", code == 0 and out == "")

    # A second compaction: the answer arrived and the task finished; the agent is still out.
    write(tr, entries + [user("yes bump it"), notification("bsynth01"),
                         {"type": "system", "subtype": "compact_boundary"},
                         user("carry on"), assistant(text("Bumped."))])
    precompact(home, tr)
    rec2 = json.load(open(rec_path, encoding="utf-8"))
    check("running agent from the previous record carries over", [a["id"] for a in rec2["agents"]] == ["asynthagent01"])
    check("previous PRs carry over", any(p["number"] == 41 for p in rec2["prs"]))
    check("answered question is gone", rec2["pending_questions"] == [] and rec2["last_user_message"] == "carry on")
    write(tr, [notification("asynthagent01"), user("next")])
    precompact(home, tr)
    rec3 = json.load(open(rec_path, encoding="utf-8"))
    check("carried-over agent dropped once its notification arrives", rec3["agents"] == [])

    # AskUserQuestion with no tool_result is pending.
    write(tr, [user("pick one"), assistant(tool_use("q1", "AskUserQuestion",
                                                    questions=[{"question": "Which option do you want?"}]))])
    precompact(home, tr, session="ask-session")
    rec4 = json.load(open(cc.record_path("ask-session"), encoding="utf-8"))
    check("AskUserQuestion without an answer is pending", "Which option do you want?" in rec4["pending_questions"])

    # Cap: a flood of files and PRs stays under the injected cap.
    flood = [user("go")]
    for i in range(200):
        flood.append(assistant(tool_use("w%d" % i, "Write", file_path="/work/proj/" + "deep/" * 20 + "f%d.txt" % i,
                                        content="x"),
                               text("see https://github.com/%s/pull/%d" % (REPO, 100 + i))))
    write(tr, flood)
    precompact(home, tr, session="flood")
    rflood = json.load(open(cc.record_path("flood"), encoding="utf-8"))
    check("record lists are capped", len(rflood["files"]) == 15 and len(rflood["prs"]) == 10)
    _, _, ctx = start(home, session="flood")
    check("injected text is capped", 0 < len(ctx) <= cc.MAX_CONTEXT)

    # Session id changed across compaction: fall back to a fresh record for the same cwd.
    _, _, ctx = start(home, session="new-id-after-compact", cwd="/work/proj")
    check("fallback finds a fresh record for the same cwd", ctx.startswith("Carried over"))
    code, out, _ = start(home, session="new-id-after-compact", cwd="/elsewhere")
    check("no fallback for another cwd", code == 0 and out == "")

    # Fails silent.
    code, out = precompact(home, os.path.join(root, "missing.jsonl"), session="missing")
    check("missing transcript: exit 0, no record", code == 0 and out == ""
          and not os.path.exists(cc.record_path("missing")))
    code, out = run(home, "not json at all")
    check("garbage stdin: exit 0", code == 0 and out == "")
    code, out = run(home, {"hook_event_name": "SessionStart", "session_id": "none-here", "source": "compact",
                           "cwd": "/nowhere"})
    check("no record: SessionStart quiet", code == 0 and out == "")
    code, out = run(home, {"hook_event_name": "PreCompact", "session_id": "off2", "transcript_path": tr},
                    {"GNOMONKIT_CARRYOVER": "off"})
    check("GNOMONKIT_CARRYOVER=off writes nothing", code == 0 and not os.path.exists(cc.record_path("off2")))
    with open(cc.record_path("broken"), "w", encoding="utf-8") as fh:
        fh.write("{garbage")
    code, out = precompact(home, tr, session="broken")
    check("garbage previous record is replaced", code == 0 and json.load(open(cc.record_path("broken"),
                                                                              encoding="utf-8"))["version"] == 1)

    # The tail read starts mid-file: a cut first line is skipped, not fatal.
    big = [user("x" * 1000) for _ in range(50)] + [user("the real last message")]
    write(tr, big)
    entries_tail = cc.read_tail(tr, limit=3000)
    check("tail read skips the cut line and keeps the end",
          cc.extract(entries_tail)["last_user_message"] == "the real last message")

    # Work items: the claimed, unclosed ones from `gnomonkit work`'s local record, never GitHub.
    wsession = "work:session/1"
    wdir = os.path.join(home, "work", "sessions")
    os.makedirs(wdir, exist_ok=True)
    wfile = os.path.join(wdir, "work_session_1.json")
    with open(wfile, "w", encoding="utf-8") as fh:
        json.dump({"session_id": wsession, "items": {
            REPO + "#7": {"repo": REPO, "number": 7, "title": "Skip empty rows <b>now</b>", "claimed": True,
                          "touched": 2},
            REPO + "#8": {"repo": REPO, "number": 8, "title": "Already done", "claimed": False, "closed": True},
            REPO + "#9": {"repo": REPO, "number": 9, "title": "Only commented on", "claimed": False},
            "bad": {"repo": "not a repo", "number": 1, "claimed": True}}}, fh)
    check("work items: claimed and open only", [w["ref"] for w in cc.work_items(wsession)] == [REPO + "#7"])
    t7 = cc.work_items(wsession)[0]["title"]
    check("work items: titles lose markup", t7.startswith("Skip empty rows") and "<" not in t7 and ">" not in t7)
    write(tr, [user("continue with the importer")])
    code, _ = precompact(home, tr, session=wsession)
    code2, _, ctx = start(home, session=wsession)
    check("work items carried over with the read-first hint",
          code == 0 and code2 == 0 and ("%s#7 (Skip empty rows" % REPO) in ctx
          and "gnomonkit work show" in ctx and "#8" not in ctx and "#9" not in ctx)
    with open(wfile, "w", encoding="utf-8") as fh:
        json.dump({"items": {REPO + "#7": {"repo": REPO, "number": 7, "claimed": False, "closed": True}}}, fh)
    _, _, ctx = start(home, session=wsession)
    check("an item closed after PreCompact is not carried", "Work items" not in ctx)
    with open(wfile, "w", encoding="utf-8") as fh:
        fh.write("{garbage")
    check("a broken work record means no items", cc.work_items(wsession) == [])

print("\n%d failure(s)" % len(FAILS))
sys.exit(1 if FAILS else 0)
