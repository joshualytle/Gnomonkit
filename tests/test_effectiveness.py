"""effectiveness.py on synthetic transcripts: every metric's arithmetic, from fake files in a temp dir."""
import datetime
import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "core" / "skills" / "effectiveness" / "effectiveness.py"
spec = importlib.util.spec_from_file_location("effectiveness", SCRIPT)
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)

NOW = time.time()
TS = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - 3600))
TU = "tool" + "u_"          # tool_use id prefix, built so no file holds a real-looking id


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """No test reads or writes the real ~/.gnomonkit (subscription file, correction sample, bench results)."""
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk-default"))
    monkeypatch.setenv("GNOMONKIT_BENCH_DIR", str(tmp_path / "bench-default"))
    for k in ("CLAUDE_PLUGIN_OPTION_SUBSCRIPTION_FILE", "GNOMONKIT_SUBSCRIPTION_FILE",
              "CLAUDE_PLUGIN_OPTION_PERSONAL_PROFILE", "GNOMONKIT_PERSONAL_PROFILE"):
        monkeypatch.delenv(k, raising=False)


def a_turn(mid, *blocks, usage=None):
    return {"type": "assistant", "timestamp": TS, "cwd": "/work/demo-app",
            "message": {"id": mid, "role": "assistant", "content": list(blocks),
                        "usage": usage or {"input_tokens": 1, "output_tokens": 1}}}


def tool_use(tid, name, **inp):
    return {"type": "tool_use", "id": TU + tid, "name": name, "input": inp}


def text(t):
    return {"type": "text", "text": t}


def result(tid, content, is_error=False, tur=None):
    d = {"type": "user", "timestamp": TS,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": TU + tid,
                                                  "content": content, "is_error": is_error}]}}
    if tur is not None:
        d["toolUseResult"] = tur
    return d


def prompt(t):
    return {"type": "user", "timestamp": TS, "message": {"role": "user", "content": t}}


def hook(event, tid, body):
    return {"type": "attachment", "timestamp": TS,
            "attachment": {"type": "hook_additional_context", "hookEvent": event, "toolUseID": TU + tid,
                           "content": [body]}}


def queued(p):
    return {"type": "attachment", "timestamp": TS, "attachment": {"type": "queued_command", "prompt": p}}


def write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, separators=(",", ":")) + "\n" for x in lines), encoding="utf-8")


LAUNCH = "Async agent launched successfully.\nagentId: bbb2 (internal ID)"
NOTIF = ("<task-notification>\n<task-id>bbb2</task-id>\n<tool-use-id>" + TU + "B</tool-use-id>\n"
         "<status>completed</status>\n<result>short answer</result>\n"
         "<usage><subagent_tokens>999</subagent_tokens></usage>\n</task-notification>")
FG_RESULT = "agentId: aaa1\n" + "r" * 386          # 400 chars -> 100 tokens


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "claude"
    proj = h / "projects" / "demo-proj"
    # session 1: a worth-it foreground spawn (A) and a not-worth-it background spawn (B)
    write(proj / "s1.jsonl", [
        prompt("start"),
        a_turn("m1", tool_use("A", "Agent", subagent_type="Explore", description="look around")),
        result("A", FG_RESULT),
        a_turn("m2", tool_use("B", "Agent", subagent_type="general-purpose", description="tiny lookup",
                              run_in_background=True)),
        result("B", [{"type": "text", "text": LAUNCH}], tur={"isAsync": True, "agentId": "bbb2"}),
        a_turn("m3", text("waiting")),
        {"type": "system", "subtype": "compact_boundary", "timestamp": TS},
        queued(NOTIF),
        prompt(NOTIF),                                   # the same notification delivered again: counted once
        a_turn("m4", text("done")),
    ])
    sub = proj / "s1" / "subagents"
    write(sub / "agent-aaa1.jsonl", [
        prompt("explore"),
        a_turn("ma1", *[tool_use("r%d" % i, "Read", file_path="/f%d" % i) for i in range(4)],
               usage={"input_tokens": 10, "cache_creation_input_tokens": 20,
                      "cache_read_input_tokens": 30, "output_tokens": 40}),
        *[result("r%d" % i, "x" * 4000) for i in range(4)],            # 16,000 chars -> 4,000 tokens
        a_turn("ma2", text("found it"), usage={"input_tokens": 1, "cache_creation_input_tokens": 2,
                                               "cache_read_input_tokens": 3, "output_tokens": 4}),
        a_turn("ma2", text("same message, second block"), usage={"input_tokens": 1, "cache_creation_input_tokens": 2,
                                                                 "cache_read_input_tokens": 3, "output_tokens": 4}),
    ])
    (sub / "agent-aaa1.meta.json").write_text(json.dumps(
        {"agentType": "Explore", "toolUseId": TU + "A", "spawnDepth": 1, "requestShape": "foreground"}))
    write(sub / "agent-bbb2.jsonl", [
        prompt("look"),
        a_turn("mb1", tool_use("g1", "Grep", pattern="x"),
               usage={"input_tokens": 0, "cache_creation_input_tokens": 1000,
                      "cache_read_input_tokens": 50000, "output_tokens": 500}),
        result("g1", "y" * 2000),                                       # 500 tokens
        a_turn("mb2", text("short answer"), usage={"output_tokens": 0}),
    ])
    (sub / "agent-bbb2.meta.json").write_text(json.dumps(
        {"agentType": "general-purpose", "toolUseId": TU + "B", "spawnDepth": 1, "requestShape": "background"}))
    # a workflow step: no toolUseId and no parent call, so spend only
    wf = sub / "workflows" / "wf_1"
    write(wf / "agent-ccc3.jsonl", [prompt("step"), a_turn("mc1", text("ok"), usage={"output_tokens": 7})])
    (wf / "agent-ccc3.meta.json").write_text(json.dumps(
        {"agentType": "workflow-subagent", "workflowPhase": "p1", "spawnDepth": 1}))

    # session 2: hooks and an output-digest wrapper
    full = tmp_path / "digests" / "full.log"
    full.parent.mkdir()
    full.write_bytes(b"z" * 20000)
    header = ("[digest-wrap] pytest: exit 0; 12,000 chars / 300 lines of output, too long to show in full.\n"
              "Full output: %s  (Read it with offset=<L number>, or grep it.)\n" % full.as_posix())
    gone = ("[digest-wrap] build: exit 1; 9,000 chars / 90 lines of output, too long to show in full.\n"
            "Full output: %s  (Read it)\n" % (tmp_path / "digests" / "gone.log").as_posix())
    write(proj / "s2.jsonl", [
        prompt("go"),
        hook("SessionStart", "", "PALACE\n  [L12 · 1 drawer · everywhere] a lesson\n  [E7] an entry"),
        hook("SessionStart", "", "GNOMONKIT: Plugin update pending for core."),
        hook("UserPromptSubmit", "", "GNOMONKIT: the user's last message reads like a correction. Address it."),
        a_turn("n1", tool_use("D1", "Bash", command="digest-wrap pytest -q")),
        result("D1", header + "d" * 900),
        a_turn("n2", tool_use("D2", "Bash", command="digest-wrap git status")),
        result("D2", "on branch main"),
        a_turn("n3", tool_use("D3", "Bash", command="digest-wrap make")),
        result("D3", gone + "e" * 400),
        a_turn("n4", tool_use("E1", "Bash", command="git push origin x")),
        hook("PreToolUse", "E1", "Skill check:\n  skill: core:ship-through-prs\n  skill: diff-review"),
        result("E1", "ok"),
        a_turn("n5", tool_use("E2", "Skill", skill="core:ship-through-prs")),
        result("E2", "loaded"),
        a_turn("n6", tool_use("E3", "Write", file_path="/tmp/x/scratchpad/page.html")),
        hook("PreToolUse", "E3", "  skill: report-pages"),
        result("E3", "ok"),
        a_turn("n7", tool_use("E4", "Read", file_path="C:\\kit\\skills\\report-pages\\SKILL.md")),
        result("E4", "skill text"),
        a_turn("n8", tool_use("F1", "Bash", command="rm -rf build")),
        result("F1", "PreToolUse:Bash hook error: [x]\n  IRREVERSIBLE ACTION BLOCKED -- enumerate", is_error=True),
        a_turn("n9", tool_use("F2", "Bash", command="ls build")),
        result("F2", "a b"),
        a_turn("n10", tool_use("F3", "Bash", command="rm -rf build  # interlock-ok: listed build, 2 files")),
        result("F3", ""),
        a_turn("n11", tool_use("G1", "Bash", command="rm -rf dist")),
        result("G1", "IRREVERSIBLE ACTION BLOCKED", is_error=True),
        a_turn("n12", tool_use("G2", "Bash", command="rm dist/one.txt")),
        result("G2", ""),
        a_turn("n13", text("as L12 says, enumerate first"), tool_use("H1", "PowerShell", command="git push --force")),
        result("H1", "IRREVERSIBLE ACTION BLOCKED", is_error=True),
        a_turn("n14", text("I will stop here.")),
    ])
    return h


@pytest.fixture
def gk(tmp_path):
    g = tmp_path / "gk"
    d = g / "interjector" / "sess1"
    d.mkdir(parents=True)
    rows = [{"kind": "guess", "id": "g1", "t": NOW - 60, "question": "q"},
            {"kind": "reply", "answers": "g1", "t": NOW - 50, "text": "yes"},
            {"kind": "guess", "id": "g2", "t": NOW - 40, "question": "q2"},
            {"kind": "guess", "id": "g3", "t": NOW - 30, "question": "q3"},
            {"kind": "reply", "answers": "g3", "t": NOW - 20, "text": "no"},
            {"kind": "guess", "id": "old", "t": NOW - 90 * 86400, "question": "q4"}]
    (d / "guesses.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (g / "interjector" / "verdicts.json").write_text(json.dumps(
        {"guess-verdicts": {"g1": {"verdict": "right"}, "g3": {"verdict": "wrong"}}}))
    (g / "freshness").mkdir()
    (g / "freshness" / "k.json").write_text(json.dumps({"checked": NOW, "stale": [["core", "m", "a", "b"]]}))
    return g


def run(home, gk, tmp_path, **kw):
    kw.setdefault("digest", "digest-wrap")
    return E.collect(30, home=home, gk=gk, cache_path=tmp_path / "cache.json", now=NOW, **kw)


def rows_by_agent(m):
    return {r["agent"]: r for r in m["subagents"]["spawn_rows"]}


def test_worth_it_foreground_spawn(home, gk, tmp_path):
    r = rows_by_agent(run(home, gk, tmp_path))["aaa1"]
    assert r["linked"] and r["type"] == "Explore" and not r["background"]
    # usage summed per message id: ma2's second line repeats the same message and is not double counted
    assert r["spend"] == {"input": 11, "cache_creation": 22, "cache_read": 33, "output": 44, "total": 110}
    assert r["kept_out"] == 4000
    assert r["returned"] == 100
    assert r["net_kept_out"] == 3900
    # result arrived after parent turn 1; the compaction after turn 3 caps the count at 2
    assert r["parent_turns_after"] == 2
    assert r["reads_avoided"] == 7800
    assert r["worth_it"] is True and r["small"] is False


def test_not_worth_it_background_spawn(home, gk, tmp_path):
    r = rows_by_agent(run(home, gk, tmp_path))["bbb2"]
    assert r["background"] and r["linked"] and r["return_seen"]
    assert r["spend"]["total"] == 51500
    assert r["kept_out"] == 500
    returned = round((len(LAUNCH) + len(NOTIF)) / 4)      # launch notice + ONE copy of the notification
    assert r["returned"] == returned
    assert r["net_kept_out"] == 500 - returned
    assert r["parent_turns_after"] == 1                   # notification arrived after turn 3; one turn followed
    assert r["reads_avoided"] == (500 - returned) * 1
    assert r["worth_it"] is False
    assert r["small"] is True                             # one tool call
    assert r["notified_tokens"] == 999


def test_unlinked_and_aggregates(home, gk, tmp_path):
    m = run(home, gk, tmp_path)
    s = m["subagents"]
    c = rows_by_agent(m)["ccc3"]
    assert not c["linked"] and c["why_unlinked"] == "workflow" and c["worth_it"] is None
    assert c["spend"]["total"] == 7
    assert (s["spawns"], s["linked"], s["unlinked"], s["worth_it"], s["not_worth_it"]) == (3, 2, 1, 1, 1)
    assert s["spend"]["total"] == 110 + 51500 + 7
    assert s["by_type"]["Explore"]["worth_it_share"] == 1.0
    assert s["by_type"]["general-purpose"]["worth_it_share"] == 0.0
    assert s["by_project"]["demo-app"]["spawns"] == 3
    y, w, _ = datetime.datetime.fromisoformat(TS[:19]).isocalendar()
    assert list(s["by_week"]) == ["%d-W%02d" % (y, w)]      # real timestamps (fractions, Z) parse to a week
    assert sum(x["spawns"] for x in s["by_week"].values()) == 3
    wk = s["by_week"]["%d-W%02d" % (y, w)]
    assert wk["partial"] is True and 0 < wk["days_in_window"] <= 7     # the week holding `now` is not over
    assert wk["linked_spend"] == 110 + 51500 and wk["spend"] == 110 + 51500 + 7   # the workflow step is unlinked
    assert s["by_type"]["Explore"]["linked_spend"] == s["by_type"]["Explore"]["spend"]
    assert [r["agent"] for r in s["not_worth_it_spawns"]] == ["bbb2"]
    h = m["headline"]
    assert h["subagent_spend"] == {"value": 51617, "label": "MEASURED"}
    assert h["tokens_saved"]["parts"]["subagents"] == s["reads_avoided"] == 7800 + rows_by_agent(m)["bbb2"]["reads_avoided"]
    assert h["tokens_saved"]["label"] == "ESTIMATED"


def test_router_split_by_source_self_invoked_and_followed(tmp_path, gk):
    """Suggestions keep the plugin that made them; already-loaded ones leave the uptake share; a
    suggestion acted on without a load shows as followed; Skill calls nobody suggested are counted apart."""
    h = tmp_path / "claude-split"
    write(h / "projects" / "p" / "s.jsonl", [
        prompt("go"),
        a_turn("k1", tool_use("S1", "Skill", skill="lib:brainstorming")),
        result("S1", "loaded"),
        a_turn("k2", tool_use("S2", "Skill", skill="core:report-pages")),
        result("S2", "loaded"),
        a_turn("k3", tool_use("S3", "Write", file_path="/w/report.html")),
        hook("PreToolUse", "S3", "  skill: core:report-pages\n  skill: lib:helper  (router: overlay)"),
        result("S3", "ok"),
        a_turn("k4", tool_use("S4", "Bash", command="gh pr merge 3 --rebase")),
        hook("PostToolUse", "S4", "  skill: overlay:merged-is-not-shipped"),
        result("S4", "merged"),
        a_turn("k5", tool_use("S5", "Bash", command="claude plugin update core@example")),
        result("S5", "updated"),
    ])
    rules = tmp_path / "rules.json"
    rules.write_text(json.dumps({"triggers": [
        {"skill": "merged-is-not-shipped", "follow_through": ["plugin\\s+update\\b", "("]}]}))   # "(" is skipped
    m = E.collect(30, home=h, gk=gk, cache_path=None, now=NOW, triggers=[rules, tmp_path / "missing.json"])
    r = m["hooks"]["router"]
    assert r["report-pages"]["already_loaded"] == 1 and r["report-pages"]["open"] == 0
    assert r["report-pages"]["uptake"] is None
    assert r["helper"]["by"] == {"overlay": 1}                     # the router mark wins over the name's prefix
    ship = r["merged-is-not-shipped"]
    assert ship["by"] == {"overlay": 1} and ship["taken"] == 0 and ship["uptake"] == 0.0
    assert ship["followed"] == 1 and ship["follow_through"] == 1.0
    assert m["hooks"]["skills_self"] == {"lib:brainstorming": 1, "core:report-pages": 1}
    su = by_id(m)["skill_uptake"]
    assert su["n"] == 1 and su["value"] == 0.0                     # the already-loaded one is out of n
    d = su["detail"]
    assert (d["suggestions"], d["suggestions_open"], d["suggestions_already_loaded"]) == (2, 1, 1)


def test_digest(home, gk, tmp_path):
    d = run(home, gk, tmp_path)["digest"]
    assert d["calls"] == 3 and d["known"] == 3
    assert d["by_source"] == {"file": 1, "passthrough": 1, "header": 1}
    h1 = len("[digest-wrap] pytest: exit 0; 12,000 chars / 300 lines of output, too long to show in full.\n") + \
        len("Full output: %s  (Read it with offset=<L number>, or grep it.)\n" % (tmp_path / "digests" / "full.log").as_posix()) + 900
    h3 = len("[digest-wrap] build: exit 1; 9,000 chars / 90 lines of output, too long to show in full.\n") + \
        len("Full output: %s  (Read it)\n" % (tmp_path / "digests" / "gone.log").as_posix()) + 400
    saved1, saved3 = 20000 - h1, 9000 - h3                 # file size where it exists, else the header's count
    assert d["saved_chars"] == saved1 + saved3
    assert d["saved_tokens"] == round((saved1 + saved3) / 4)
    # D1's result arrived after turn n1 (13 later turns), D3's after n3 (11 later turns)
    assert d["reads_avoided"] == round(saved1 / 4) * 13 + round(saved3 / 4) * 11


def test_digest_is_a_setting(home, gk, tmp_path, monkeypatch):
    """No wrapper named: nothing is tracked. The name comes from the option or the env var."""
    monkeypatch.delenv("GNOMONKIT_DIGEST_COMMAND", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_DIGEST_COMMAND", raising=False)
    off = E.collect(30, home=home, gk=gk, cache_path=None, now=NOW)["digest"]
    assert off["command"] is None and off["calls"] == 0
    monkeypatch.setenv("GNOMONKIT_DIGEST_COMMAND", "digest-wrap")
    on = E.collect(30, home=home, gk=gk, cache_path=None, now=NOW)["digest"]
    assert on["command"] == "digest-wrap" and on["calls"] == 3
    other = run(home, gk, tmp_path, digest="other-wrap")["digest"]
    assert other["calls"] == 0, "a different name must not match another wrapper's calls (cache keyed on it too)"


def test_hooks(home, gk, tmp_path):
    hk = run(home, gk, tmp_path, triggers=[])["hooks"]
    r = hk["router"]
    assert r["ship-through-prs"] == {"suggested": 1, "taken": 1, "already_loaded": 0, "scratch_paths": 0,
                                     "followed": 0, "by": {"core": 1}, "open": 1, "uptake": 1.0,
                                     "follow_through": None}
    assert r["diff-review"]["taken"] == 0 and r["diff-review"]["uptake"] == 0.0
    assert r["diff-review"]["by"] == {"unknown": 1}                # a suggestion line with no plugin prefix
    assert hk["skills_self"] == {}                                  # the one Skill call followed a suggestion
    assert r["report-pages"]["taken"] == 1 and r["report-pages"]["scratch_paths"] == 1   # taken by reading SKILL.md
    assert hk["interlock"]["blocks"] == 3
    assert hk["interlock"]["outcomes"] == {"override_after_listing": 1, "rewritten": 1, "abandoned": 1}
    assert hk["correction_watch"]["fires"] == 1
    assert hk["freshness"]["notices"] == 1
    p = hk["palace"]
    assert (p["injected"], p["cited"]) == (2, 1)            # L12 cited afterwards, E7 not
    assert p["by_channel"]["SessionStart"] == [2, 1]


def test_interjector_and_state(home, gk, tmp_path):
    m = run(home, gk, tmp_path)
    ij = m["interjector"]
    assert (ij["guesses"], ij["answered"], ij["unanswered"], ij["right"], ij["wrong"]) == (3, 2, 1, 1, 1)
    assert m["state"]["freshness_state"]["plugins_pending_now"] == 1


def test_cache_reuses_unchanged_files(home, gk, tmp_path):
    first = run(home, gk, tmp_path)
    second = run(home, gk, tmp_path)
    assert first["sources"]["scanned"] == 5 and second["sources"]["cache_hits"] == 5
    assert second["sources"]["scanned"] == 0
    assert second["subagents"]["reads_avoided"] == first["subagents"]["reads_avoided"]


def test_long_lines_still_count_turns(tmp_path):
    p = tmp_path / "long.jsonl"
    write(p, [a_turn("big1", text("w" * 5000), usage={"input_tokens": 3, "output_tokens": 9}),
              result("q", "v" * 5000), a_turn("s2", text("short"))])
    v = E.scan_file(p, line_cap=1000)
    assert v["turns"] == 2 and v["long_lines"] == 2 and v["long_est"] == 1
    assert v["usage"][0] == 3 + 1 and v["usage"][3] == 9 + 1
    assert v["kept_chars"] > 5000                          # the over-long result counted by line length


def test_since_window_excludes_old_sessions(home, gk, tmp_path):
    m = E.collect(30, home=home, gk=gk, cache_path=None, now=NOW + 400 * 86400)
    assert m["subagents"]["spawns"] == 0 and m["sources"]["transcripts"] == 0


def test_cli_writes_json(home, gk, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setenv("GNOMONKIT_HOME", str(gk))
    out = tmp_path / "m.json"
    assert E.main(["--json", str(out), "--no-cache"]) == 0
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["schema"] == 1 and m["subagents"]["spawns"] == 3
    assert "tokens saved" in capsys.readouterr().out
    # --projects points at a transcripts folder directly
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))
    assert E.main(["--json", str(out), "--no-cache", "--quiet", "--projects", str(home / "projects")]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["subagents"]["spawns"] == 3


def ctx_usage(k, cached=0.9):
    """Usage whose context (input + cache read + cache creation) is k thousand tokens."""
    n = k * 1000
    return {"input_tokens": n // 10, "cache_read_input_tokens": int(n * cached) - n // 10,
            "cache_creation_input_tokens": n - int(n * cached), "output_tokens": 5}


@pytest.fixture
def ctx_projects(tmp_path):
    proj = tmp_path / "ctx" / "projects" / "demo-proj"
    write(proj / "x1.jsonl", [
        prompt("go"),
        a_turn("c1", text("a"), usage=ctx_usage(100)),
        a_turn("c2", text("b"), usage=ctx_usage(300)),
        a_turn("c3", text("c"), usage=ctx_usage(600)),
        a_turn("c3", text("same message, second block"), usage=ctx_usage(600)),     # one API message id: once
        {"type": "system", "subtype": "compact_boundary", "timestamp": TS},
        a_turn("c4", text("d"), usage=ctx_usage(900)),
        a_turn("c5", text("no usage"), usage={"output_tokens": 0}),                 # no context: not a turn here
    ])
    write(proj / "x1" / "subagents" / "agent-ddd4.jsonl", [                          # a subagent: left out
        prompt("sub"), a_turn("d1", text("big"), usage=ctx_usage(950))])
    write(proj / "x2.jsonl", [prompt("go"), a_turn("e1", text("e"), usage=ctx_usage(50))])
    return tmp_path / "ctx" / "projects"


def test_main_context_per_turn_and_shares(ctx_projects, gk, tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET", raising=False)
    monkeypatch.delenv("GNOMONKIT_CONTEXT_BUDGET", raising=False)
    c = E.collect(30, home=tmp_path / "claude", gk=gk, cache_path=None, now=NOW, projects=ctx_projects,
                  digest="")["main_context"]
    assert c["budget"] == 250_000
    assert c["sessions"] == 2 and c["turns"] == 5
    assert c["reread"] == 1_950_000 and c["peak"] == 900_000 and c["median"] == 300_000
    assert c["compacted_sessions"] == 1 and c["compactions"] == 1
    assert c["over"]["200000"] == {"turns_share": 0.6, "reread_share": round(1800 / 1950, 3)}
    assert c["over"]["300000"] == {"turns_share": 0.4, "reread_share": round(1500 / 1950, 3)}   # strictly above
    assert c["over"]["800000"] == {"turns_share": 0.2, "reread_share": round(900 / 1950, 3)}
    assert c["reread_capped"] == 100_000 + 3 * 250_000 + 50_000
    assert c["reread_over_budget"] == 1_950_000 - c["reread_capped"]
    assert c["labels"]["reread"] == "MEASURED" and c["labels"]["reread_capped"] == "ESTIMATED"
    top = c["per_session"][0]
    assert top["session"] == "x1" and top["turns"] == 4 and top["peak"] == 900_000
    assert top["reread"] == 1_900_000 and top["compactions"] == 1


def test_context_budget_is_a_setting(ctx_projects, gk, tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET", raising=False)
    monkeypatch.setenv("GNOMONKIT_CONTEXT_BUDGET", "500000")
    assert E.context_budget() == 500_000
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET", "100,000")
    assert E.context_budget() == 100_000
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET", "nonsense")
    monkeypatch.delenv("GNOMONKIT_CONTEXT_BUDGET")
    assert E.context_budget() == 250_000
    # no option at all: the personal profile, then the default
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET")
    profile = tmp_path / "profile.toml"
    profile.write_text("[context]\ncontext_budget = 180000\n", encoding="utf-8")
    monkeypatch.setenv("GNOMONKIT_PERSONAL_PROFILE", str(profile))
    assert E.context_budget() == 180_000
    monkeypatch.setenv("GNOMONKIT_CONTEXT_BUDGET", "400000")
    assert E.context_budget() == 400_000
    c = E.collect(30, home=tmp_path / "claude", gk=gk, cache_path=None, now=NOW, projects=ctx_projects,
                  digest="", budget=1_000_000)["main_context"]
    assert c["reread_capped"] == c["reread"] and c["reread_over_budget"] == 0


def test_main_context_in_table_and_json(ctx_projects, gk, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("GNOMONKIT_HOME", str(gk))
    out = tmp_path / "eff.json"
    assert E.main(["--projects", str(ctx_projects), "--no-cache", "--json", str(out), "--context-budget", "300000"]) == 0
    printed = capsys.readouterr().out
    assert "MAIN CONTEXT" in printed and "MEASURED" in printed and "ESTIMATED" in printed
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["main_context"]["budget"] == 300_000 and m["method"]["context_budget"] == 300_000


def test_main_context_empty_window(gk, tmp_path):
    m = E.collect(30, home=tmp_path / "claude", gk=gk, cache_path=None, now=NOW, projects=tmp_path / "none")
    c = m["main_context"]
    assert c["turns"] == 0 and c["peak"] is None and c["over"]["200000"]["turns_share"] is None
    assert "no main-session turns" in E.table(m)


# ---------------------------------------------------------------------------
# compaction and budgets
# ---------------------------------------------------------------------------

def ctx_turn(mid, tokens, *blocks):
    return a_turn(mid, *blocks, usage={"input_tokens": tokens, "output_tokens": 1})


def read(i, path):
    return ctx_turn("r%s" % i, 0, tool_use("rd%s" % i, "Read", file_path=path))


def compact(trigger="auto", pre=180_000):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": TS, "content": "compacted",
            "compactMetadata": {"trigger": trigger, "preTokens": pre}}


def harm_session(tmp_path, n, corrected=True):
    """A session whose compaction is followed by re-reads of earlier files and (optionally) a correction."""
    src = ["/work/demo-app/src/f%d.py" % i for i in range(12)]
    lines = [prompt("start"), ctx_turn("m%d1" % n, 20_000), ctx_turn("m%d2" % n, 30_000),
             ctx_turn("m%d3" % n, 25_000), prompt("next task"), ctx_turn("m%d4" % n, 60_000),
             ctx_turn("m%d5" % n, 90_000)]
    lines += [ctx_turn("p%d_%d" % (n, i), 100_000, tool_use("pr%d_%d" % (n, i), "Read", file_path=p))
              for i, p in enumerate(src)]
    lines.append(compact())
    lines += [ctx_turn("q%d_%d" % (n, i), 40_000, tool_use("qr%d_%d" % (n, i), "Read", file_path=p))
              for i, p in enumerate(src[:8] + ["/work/demo-app/src/new%d.py" % i for i in range(2)])]
    lines.append(prompt("you didn't run the tests like I asked" if corrected else "carry on"))
    lines.append(ctx_turn("m%d9" % n, 45_000))
    return lines


def test_compaction_events_measure_reread_and_correction(tmp_path):
    f = tmp_path / "s.jsonl"
    write(f, harm_session(tmp_path, 1))
    v = E.scan_file(f)
    assert E.is_correction is not None and E.is_correction("you didn't run the tests like I asked")
    (ev,) = v["compactions"]
    assert ev["trigger"] == "auto" and ev["ctx"] == 180_000
    assert ev["post_reread"] == 0.8 and ev["pre_reread"] == 0.0 and ev["reads_after"] == 10
    assert ev["corrected"] is True
    assert v["prompts"] == 3
    assert sorted(v["growth"]) == [5_000, 75_000]     # 25k -> peak 100k before the compaction; 40k -> 45k


def project_stats(**kw):
    p = {"cwd": None, "sessions": 10, "prompts": 60, "base": 40_000, "growth_median": 30_000,
         "growth_p90": 140_000, "peaks": [150_000] * 10, "compacted_sessions": 5,
         "compaction": {"count": 6, "measured": 6, "reread_pre": 0.10, "reread_post": 0.12,
                        "corrected": 0, "correction_checked": 6}}
    comp = kw.pop("compaction", {})
    p.update(kw)
    p["compaction"] = dict(p["compaction"], **comp)
    return p


def test_harmful_compactions_raise_one_step():
    r = E.recommend_budget(project_stats(compaction={"reread_post": 0.45}), 200_000)
    assert r["start"] == 200_000 and r["recommended"] == 250_000 and r["rule"] == "raise_harm"
    assert r["confidence"] == "medium" and "harm" in r["reason"]
    r = E.recommend_budget(project_stats(compaction={"corrected": 3}), 200_000)
    assert r["recommended"] == 250_000


def test_harm_needs_three_compactions():
    r = E.recommend_budget(project_stats(compaction={"reread_post": 0.9, "measured": 2, "count": 2}), 200_000)
    assert r["rule"] != "raise_harm" and r["recommended"] == 200_000


def test_harmless_stays_or_lowers():
    r = E.recommend_budget(project_stats(), 200_000)
    assert r["recommended"] == 200_000 and r["rule"] == "base_growth"
    r = E.recommend_budget(project_stats(peaks=[260_000] * 5 + [100_000] * 5), 200_000)
    assert r["recommended"] == 150_000 and r["rule"] == "lower_harmless"
    r = E.recommend_budget(project_stats(growth_p90=30_000, base=180_000, compacted_sessions=0,
                                         compaction={"count": 0, "measured": 0}), 250_000)
    assert r["start"] == 250_000 and r["recommended"] == 200_000 and r["rule"] == "lower_rare"


def test_clamped_to_range():
    r = E.recommend_budget(project_stats(base=300_000, growth_p90=400_000, compaction={"reread_post": 0.9}),
                           300_000)
    assert r["recommended"] == E.BUDGET_MAX
    r = E.recommend_budget(project_stats(base=10_000, growth_p90=20_000, compacted_sessions=0,
                                         compaction={"count": 0, "measured": 0}), 250_000)
    assert r["recommended"] == E.BUDGET_MIN


def test_low_sample_is_low_confidence_and_unchanged():
    r = E.recommend_budget(project_stats(sessions=2, prompts=6, compaction={"reread_post": 0.9, "count": 1}), 300_000)
    assert r["confidence"] == "low" and r["recommended"] == 300_000 and r["change"] == 0


def test_confidence_levels():
    assert E.confidence(4, 500) == "low" and E.confidence(5, 20) == "medium" and E.confidence(20, 100) == "high"


def test_recommend_reads_project_budget_and_prints_json(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.local.json").write_text(json.dumps({"env": {"GNOMONKIT_CONTEXT_BUDGET": "300000"}}))
    assert E.project_budget(str(repo), 250_000) == (300_000, "settings.local.json")
    assert E.project_budget(str(tmp_path / "none"), 250_000) == (250_000, "global")
    projects = tmp_path / "claude" / "projects"
    for n in range(7):
        write(projects / "harm-proj" / ("s%d.jsonl" % n), harm_session(tmp_path, n))
    rc = E.main(["--projects", str(projects), "--no-cache", "--recommend", "--since", "30"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    (row,) = out["projects"]
    assert row["project"] == "harm-proj" and row["compactions"] == 7 and row["sessions"] == 7
    assert row["rule"] == "raise_harm" and row["confidence"] == "medium"
    assert row["recommended"] == row["start"] + E.BUDGET_STEP


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------

def hook_run(kind, event, tid, script, ms, **extra):
    a = {"type": kind, "hookEvent": event, "toolUseID": TU + tid, "hookName": event + ":Bash",
         "command": 'python "${CLAUDE_PLUGIN_ROOT}/hooks/%s"' % script, "durationMs": ms}
    a.update(extra)
    return {"type": "attachment", "timestamp": TS, "attachment": a}


def by_id(m):
    return {r["id"]: r for r in m["readiness"]["rows"]}


@pytest.fixture
def ready_home(tmp_path):
    h = tmp_path / "claude"
    proj = h / "projects" / "ready-proj"
    write(proj / "r1.jsonl", [
        prompt("you didn't run the tests before saying it passed"),           # strict correction
        prompt("make sure you always add a changelog entry"),                         # fires, not strict
        prompt("please add a section about install"),                          # no fire
        a_turn("q1", tool_use("K1", "Bash", command="rm -rf out")),
        result("K1", "IRREVERSIBLE ACTION BLOCKED", is_error=True),
        prompt("go ahead, it's fine, out is a build folder"),
        a_turn("q2", tool_use("K2", "Bash", command="rm -rf out  # interlock-ok: user confirmed")),
        result("K2", ""),
        a_turn("q2b", tool_use("K4", "Bash", command="rm -rf ./cache/")),
        result("K4", "IRREVERSIBLE ACTION BLOCKED", is_error=True),
        a_turn("q2c", tool_use("K5", "Bash", command='rm -r -f "cache"')),            # same effect, got through
        result("K5", ""),
        a_turn("q2d", tool_use("K6", "Bash", command="git push --force origin main")),
        result("K6", "IRREVERSIBLE ACTION BLOCKED", is_error=True),
        a_turn("q2e", tool_use("K7", "Bash", command="git push origin main")),          # safer: not equivalent
        result("K7", ""),
        a_turn("q3", tool_use("L1", "Bash", command="python scripts/leakcheck.py --all")),
        result("L1", "a.md:3: private-name\nleakcheck: 1 finding(s). Matched text is never printed"),
        a_turn("q4", tool_use("L2", "Bash", command="python scripts/provenance_check.py --stdin pr")),
        result("L2", ""),
        a_turn("q5", tool_use("L3", "Bash", command="cat scripts/leakcheck.py")),   # reading it is not a run
        result("L3", "source"),
        hook_run("hook_success", "PreToolUse", "K1", "destructive_interlock.py", 120),
        hook_run("hook_non_blocking_error", "PreToolUse", "K2", "skill_router.py", 80),
        hook_run("hook_cancelled", "SessionStart", "S0", "plugin_freshness.py", 15600, timedOut=True),
        hook_run("hook_success", "PreToolUse", "K1", "destructive_interlock.py", 120),   # same run twice: once
        hook_run("hook_non_blocking_error", "PreToolUse", "K3", "someone_elses_hook.py", 99999),  # not core
        {"type": "system", "subtype": "stop_hook_summary", "timestamp": TS, "toolUseID": "stop1",
         "hookInfos": [{"command": 'python "${CLAUDE_PLUGIN_ROOT}/hooks/context_watch.py"', "durationMs": 40}],
         "hookErrors": []},
    ])
    write(proj / "r2.jsonl", [
        prompt("start"),
        a_turn("q6", tool_use("M1", "Read", file_path="/a")),
        hook("PreToolUse", "M1", "  skill: core:diff-review"),
        result("M1", "x"),
        a_turn("q7", text("ok")),
    ])
    return h


def ready(home, tmp_path, **kw):
    return E.collect(30, home=home, gk=tmp_path / "gk", cache_path=None, now=NOW,
                     sample_path=tmp_path / "sample.jsonl", bench_dir=tmp_path / "bench", **kw)


def test_readiness_rows_from_transcripts(ready_home, tmp_path):
    m = ready(ready_home, tmp_path)
    r = by_id(m)
    assert list(r) == ["skill_uptake", "interlock_false_blocks", "correction_precision", "hook_latency_p95",
                       "hook_errors", "bench_applied", "leak_gate_catches"]
    su = r["skill_uptake"]
    assert su["n"] == 1 and su["value"] == 0.0 and su["detail"]["sessions"] == 2   # suggested, never invoked
    ib = r["interlock_false_blocks"]
    assert (ib["n"], ib["detail"]["user_override"], ib["detail"]["equivalent_retry"]) == (3, 1, 1)
    assert ib["value"] == round(2 / 3, 3)
    cp = r["correction_precision"]
    assert cp["detail"]["fires"] == 2 and cp["detail"]["strict_agree"] == 1 and cp["value"] == 0.5
    assert cp["label"].startswith("ESTIMATED") and cp["status"] == "no-data"        # 2 < min_n
    hl, he = r["hook_latency_p95"], r["hook_errors"]
    assert he["n"] == 4 and he["detail"]["errors"] == 2                              # other hook ignored
    assert he["detail"]["kinds"] == {"error": 1, "timed_out": 1}
    assert hl["n"] == 4 and hl["value"] == 120
    assert set(hl["detail"]["by_script"]) == {"destructive_interlock.py", "skill_router.py",
                                              "plugin_freshness.py", "context_watch.py"}
    assert r["bench_applied"]["status"] == "no-data" and r["bench_applied"]["n"] == 0
    lg = r["leak_gate_catches"]
    assert (lg["n"], lg["detail"]["catches"], lg["detail"]["by_gate"]) == (2, 1, {"leakcheck": 1})
    for row in r.values():
        assert row["status"] in ("ok", "warn", "fail", "no-data") and row["target"] and row["definition"]


def test_equivalent_command_proxy():
    same = E._same_effect
    s = E._cmd_shape
    assert same(s("rm -rf ./build/"), s('rm -r -f "build" 2>/dev/null'))
    assert same(s("cd /work && rm -rf build | tail -1"), s("cd /other && rm -fr build"))
    assert not same(s("cd /work && rm -rf build"), s("cd /work && ls build"))          # destructive step dropped
    assert not same(s("git push --force origin x"), s("git push origin x"))            # flag dropped
    assert not same(s("rm -rf dist"), s("rm dist/one.txt"))                            # narrower target
    assert not same(s("cd /work"), s("cd /work"))                                      # nothing destructive
    assert s("rm -rf x  # interlock-ok: listed") == s("rm -rf x")


def test_readiness_status_bounds():
    hi = {"higher": True, "ok": 0.8, "warn": 0.6, "min_n": 3}
    lo = {"higher": False, "ok": 0.1, "warn": 0.25, "min_n": 3}
    assert [E.readiness_status(v, 5, hi) for v in (0.9, 0.7, 0.5)] == ["ok", "warn", "fail"]
    assert [E.readiness_status(v, 5, lo) for v in (0.05, 0.2, 0.5)] == ["ok", "warn", "fail"]
    assert E.readiness_status(0.9, 2, hi) == "no-data"
    assert E.readiness_status(None, 9, hi) == "no-data"


def test_part_weeks_at_either_end_of_the_window_are_marked():
    # Window: Wednesday noon of 2026-W02 to Tuesday noon of 2026-W04 (UTC).
    now = datetime.datetime(2026, 1, 20, 12, tzinfo=datetime.timezone.utc).timestamp()
    weeks = {k: {"spawns": 1} for k in ("2026-W02", "2026-W03", "2026-W04", "unknown")}
    got = E._mark_weeks(weeks, "2026-01-07T12:00:00", now)
    assert [(got[k]["days_in_window"], got[k]["partial"]) for k in ("2026-W02", "2026-W03", "2026-W04")] == [
        (4.5, True), (7.0, False), (1.5, True)]
    assert got["unknown"]["days_in_window"] is None and got["unknown"]["partial"] is None
    assert E._mark_weeks({"2020-W53": {}}, "2021-01-01T00:00:00", now)["2020-W53"]["partial"] is True


def test_existing_fixture_interlock_proxy(home, gk, tmp_path):
    r = by_id(run(home, gk, tmp_path))
    ib = r["interlock_false_blocks"]
    assert ib["n"] == 3 and ib["detail"]["rewritten_any"] == 1 and ib["detail"]["user_override"] == 0
    assert ib["detail"]["equivalent_retry"] == 0 and ib["value"] == 0.0   # rm of one file is narrower, not equal
    su = r["skill_uptake"]
    # Three core skills suggested: ship-through-prs invoked, report-pages taken by reading its SKILL.md,
    # diff-review never used. Two of three, where counting sessions gave one of one.
    assert su["n"] == 3 and su["value"] == round(2 / 3, 3) and su["display"] == "67%"
    d = su["detail"]
    assert (d["suggestions"], d["suggestions_taken"], d["suggestions_already_loaded"]) == (3, 2, 0)
    assert (d["sessions_suggested"], d["sessions_invoked_after_suggestion"]) == (1, 1)


def test_correction_sample_labels_are_kept_and_counted(tmp_path, ready_home):
    sample = tmp_path / "sample.jsonl"
    ready(ready_home, tmp_path)
    rows = [json.loads(x) for x in sample.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and all(r["label"] == "" and r["text"] for r in rows)
    # hand-label: enough synthetic labelled rows to pass min_n, one of them "no"
    extra = [{"key": "k%d" % i, "session": "s", "ts": TS, "strict": False, "text": "t", "label": "yes"}
             for i in range(9)]
    rows[0]["label"], rows[1]["label"] = "yes", "no"
    sample.write_text("".join(json.dumps(r) + "\n" for r in rows + extra), encoding="utf-8")
    cp = by_id(ready(ready_home, tmp_path))["correction_precision"]
    assert cp["label"].startswith("MEASURED") and cp["n"] == 11 and cp["value"] == round(10 / 11, 3)
    assert cp["status"] == "ok"
    again = [json.loads(x) for x in sample.read_text(encoding="utf-8").splitlines()]
    assert len(again) == 11, "a rerun must not duplicate rows or drop labels"


def test_bench_reads_latest_results(ready_home, tmp_path):
    def rec(sc, arm, applied, pol="positive", err=None, kind=None):
        return {"scenario": sc, "family": "skills", "polarity": pol, "arm": arm, "applied": applied,
                "error": err, "error_kind": kind}
    old = tmp_path / "bench" / "20200101-000000"
    new = tmp_path / "bench" / "20200102-000000"
    for d in (old, new):
        d.mkdir(parents=True)
    (old / "results.json").write_text(json.dumps({"model": "m", "records": [rec("a", "gnomonkit", False)]}))
    (new / "results.json").write_text(json.dumps({"model": "m", "records": [
        rec("a", "gnomonkit", True), rec("a", "gnomonkit", True), rec("b", "gnomonkit", False),
        rec("b", "gnomonkit", None, err="no cli", kind="infra"), rec("a", "bare", False),
        rec("c", "gnomonkit", True, pol="negative-control")]}))
    t = time.time()
    os.utime(old / "results.json", (t - 100, t - 100))
    os.utime(new / "results.json", (t, t))
    b = by_id(ready(ready_home, tmp_path))["bench_applied"]
    assert (b["n"], b["value"], b["status"]) == (3, round(2 / 3, 3), "warn")
    cells = {(c["scenario"], c["arm"]): c for c in b["detail"]["cells"]}
    assert cells[("b", "gnomonkit")]["errors"] == 1 and cells[("b", "gnomonkit")]["n"] == 1
    assert cells[("a", "bare")]["rate"] == 0.0 and b["detail"]["round"] == new.name


def test_readiness_in_table(ready_home, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(ready_home))
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    monkeypatch.setenv("GNOMONKIT_BENCH_DIR", str(tmp_path / "bench"))
    assert E.main(["--no-cache"]) == 0
    out = capsys.readouterr().out
    assert "READINESS" in out and "Interlock false blocks" in out and "no-data" in out
    assert (tmp_path / "gk" / "effectiveness" / "correction-sample.jsonl").exists()


# ---------------------------------------------------------------------------
# usage
# ---------------------------------------------------------------------------

def u_turn(mid, model, inp, cw, cr, out, ts=TS, h1=0, speed="standard"):
    return {"type": "assistant", "timestamp": ts, "cwd": "/work/usage-app", "sessionId": "u1",
            "message": {"id": mid, "model": model, "role": "assistant", "content": [text("x")],
                        "usage": {"input_tokens": inp, "cache_creation_input_tokens": cw,
                                  "cache_read_input_tokens": cr, "output_tokens": out, "speed": speed,
                                  "cache_creation": {"ephemeral_1h_input_tokens": h1,
                                                     "ephemeral_5m_input_tokens": cw - h1}}}}


def test_turn_cost_prices():
    assert E.turn_cost("claude-opus-5-5", 1000, 2000, 10000, 500, cw1h=1000) == pytest.approx(0.029)
    assert E.turn_cost("claude-opus-5-5[1m]", 1_000_000, 0, 0, 0) == pytest.approx(4.0)
    assert E.turn_cost("claude-haiku-4-5-20251001", 0, 0, 0, 1_000_000) == pytest.approx(5.0)
    assert E.turn_cost("claude-haiku-5-5", 1_000_000, 0, 0, 0) == pytest.approx(0.5)     # long-prompt tier
    assert E.turn_cost("claude-haiku-5-5", 50_000, 0, 0, 0) == pytest.approx(0.005)
    assert E.turn_cost("claude-opus-5-5", 1_000_000, 0, 0, 0, speed="fast") == pytest.approx(8.0)
    assert E.turn_cost("claude-sonnet-4-6", 1, 0, 0, 0, speed="fast") is None
    assert E.turn_cost("claude-opus-5-9", 1, 1, 1, 1) is None, "an unlisted model is never priced by prefix"
    assert E.turn_cost("<synthetic>", 1, 0, 0, 0) is None


@pytest.fixture
def usage_projects(tmp_path):
    root = tmp_path / "projects"
    proj = root / "usage-proj"
    yesterday = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - 86400 - 3600))
    write(proj / "u1.jsonl", [
        {"type": "ai-title", "aiTitle": "first guess", "sessionId": "u1"},
        {"type": "custom-title", "customTitle": "Demo usage session", "sessionId": "u1"},
        prompt("go"),
        u_turn("um1", "claude-opus-5-5", 1000, 2000, 10000, 500, h1=1000),
        u_turn("um1", "claude-opus-5-5", 1000, 2000, 10000, 500, h1=1000),      # same message again
        u_turn("um2", "claude-mystery-1", 100, 0, 0, 100, ts=yesterday),
    ])
    write(proj / "u1" / "subagents" / "agent-x1.jsonl", [
        prompt("sub"),
        u_turn("ux1", "claude-sonnet-5-5", 1_000_000, 0, 0, 0),
        u_turn("um1", "claude-opus-5-5", 1000, 2000, 10000, 500, h1=1000),      # a duplicate across files
    ])
    write(root / "other-proj" / "o1.jsonl", [prompt("hi"), u_turn("om1", "claude-haiku-4-5", 0, 0, 0, 1_000_000)])
    return root


def test_usage_by_day_project_session(usage_projects, tmp_path):
    u = E.collect(30, gk=tmp_path / "gk", cache_path=None, now=NOW, projects=usage_projects)["usage"]
    assert u["label"] == "API-equivalent estimate"
    tot = u["total"]
    assert tot["turns"] == 4 and tot["unpriced_turns"] == 1 and tot["unpriced_tokens"] == 200
    assert tot["est_cost_usd"] == pytest.approx(0.029 + 2.0 + 5.0)
    assert [d["turns"] for d in u["daily"]] == [1, 3]
    assert u["daily_timezone"] == "UTC"                  # the rows are UTC calendar days, and the output says so
    assert u["daily"][0]["est_cost_usd"] == 0.0 and u["daily"][0]["unpriced_turns"] == 1
    p = {r["project"]: r for r in u["projects"]}
    assert p["usage-proj"]["label"] == "usage-app" and p["usage-proj"]["sessions"] == 1
    assert p["usage-proj"]["est_cost_usd"] == pytest.approx(2.029) and u["projects"][0]["project"] == "other-proj"
    s = {r["session"]: r for r in u["sessions"]}
    assert s["u1"]["title"] == "Demo usage session" and s["u1"]["turns"] == 3
    assert s["u1"]["input"] == 1_001_100 and s["u1"]["first_ts"] < s["u1"]["last_ts"]
    assert u["models"]["claude-mystery-1"]["est_cost_usd"] is None


def test_usage_cli(usage_projects, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    assert E.main(["--no-cache", "--projects", str(usage_projects), "--usage", "--days", "1", "--since", "7"]) == 0
    out = capsys.readouterr().out
    assert "API-equivalent estimate" in out and "Demo usage session" in out and "usage-app" in out
    assert out.count("\n  20") == 1, "--days 1 shows one daily row"


# ---------------------------------------------------------------------------
# subscription allocation (made-up plans and prices)
# ---------------------------------------------------------------------------

SUB_NOW = datetime.datetime(2026, 3, 11, tzinfo=datetime.timezone.utc).timestamp()     # 10 days into March


def sub_ts(day):
    return "2026-%sT10:00:00.000Z" % day


@pytest.fixture
def sub_projects(tmp_path):
    root = tmp_path / "projects"
    # claude-haiku-4-5 output is $5 per million tokens, so each turn's cost is a round number
    write(root / "proj-a" / "a1.jsonl", [prompt("a"),
                                         u_turn("sa1", "claude-haiku-4-5", 0, 0, 0, 1_000_000, ts=sub_ts("02-10")),
                                         u_turn("sa2", "claude-haiku-4-5", 0, 0, 0, 1_000_000, ts=sub_ts("03-05"))])
    write(root / "proj-b" / "b1.jsonl", [prompt("b"),
                                         u_turn("sb1", "claude-haiku-4-5", 0, 0, 0, 3_000_000, ts=sub_ts("02-12")),
                                         u_turn("sb2", "claude-haiku-4-5", 0, 0, 0, 1_000_000, ts=sub_ts("03-06"))])
    return root


def sub_usage(projects, tmp_path, schedule=None, raw=None):
    f = tmp_path / "subscription.json"
    if schedule is not None:
        f.write_text(json.dumps(schedule), encoding="utf-8")
    elif raw is not None:
        f.write_text(raw, encoding="utf-8")
    return E.collect(40, gk=tmp_path / "gk", cache_path=None, now=SUB_NOW, projects=projects,
                     subscription_path=f)["usage"]


PLANS = [{"from": "2026-03", "plan": "Small", "monthly_usd": 93},      # out of order on purpose
         {"from": "2026-01", "plan": "Big", "monthly_usd": 310}]


def test_plan_for_takes_the_latest_entry_at_or_before_the_month():
    sched = sorted(({"from": p["from"], "plan": p["plan"], "monthly_usd": float(p["monthly_usd"])} for p in PLANS),
                   key=lambda r: r["from"])
    assert E.load_subscription(None)["status"] == "missing"
    assert E.plan_for("2025-12", sched) is None
    assert E.plan_for("2026-02", sched)["plan"] == "Big"
    assert E.plan_for("2026-03", sched)["plan"] == "Small" and E.plan_for("2027-01", sched)["plan"] == "Small"
    assert E._days_in_month("2026-02") == 28 and E._days_in_month("2028-02") == 29 and E._days_in_month("2026-12") == 31


def test_subscription_allocation_across_a_plan_change(sub_projects, tmp_path):
    u = sub_usage(sub_projects, tmp_path, PLANS)
    assert u["subscription"]["status"] == "ok" and [r["from"] for r in u["subscription"]["schedule"]] == \
        ["2026-01", "2026-03"]
    mo = {m["month"]: m for m in u["months"]}
    # window: Jan 30-31 (2 days), all of February, March 1-10 (10 days)
    assert (mo["2026-01"]["window_days"], mo["2026-02"]["window_days"], mo["2026-03"]["window_days"]) == (2, 28, 10)
    assert mo["2026-02"]["covers_full_month"] and not mo["2026-03"]["covers_full_month"]
    assert (mo["2026-02"]["plan"], mo["2026-02"]["subscription_usd"], mo["2026-03"]["plan"]) == ("Big", 310, "Small")
    assert mo["2026-01"]["subscription_usd_window"] == pytest.approx(20)        # 310 / 31 x 2
    assert mo["2026-03"]["subscription_usd_window"] == pytest.approx(30)        # 93 / 31 x 10
    assert (mo["2026-02"]["api_equiv_usd"], mo["2026-03"]["api_equiv_usd"]) == (20, 10)
    assert mo["2026-02"]["value_multiple"] == pytest.approx(0.06, abs=0.005)
    assert mo["2026-03"]["value_multiple"] == pytest.approx(0.33, abs=0.005)
    days = {d["date"]: d for d in u["daily"]}
    assert days["2026-02-10"]["subscription_usd_share"] == pytest.approx(310 / 28, abs=1e-3)
    assert days["2026-03-05"]["subscription_usd_share"] == pytest.approx(3.0)
    p = {r["project"]: r for r in u["projects"]}
    # Feb: A 5 of 20, B 15 of 20 of $310; Mar: each 5 of 10 of $30
    assert p["proj-a"]["subscription_usd_share"] == pytest.approx(5 / 20 * 310 + 15)
    assert p["proj-b"]["subscription_usd_share"] == pytest.approx(15 / 20 * 310 + 15)
    s = {r["session"]: r for r in u["sessions"]}
    assert s["b1"]["subscription_usd_share"] == p["proj-b"]["subscription_usd_share"]
    t = u["total"]
    assert t["subscription_usd"] == pytest.approx(360) and t["subscription_usd_allocated"] == pytest.approx(340)
    assert t["value_multiple"] == pytest.approx(30 / 360, abs=0.005)
    mtd = u["month_to_date"]
    assert (mtd["month"], mtd["plan"], mtd["subscription_usd"], mtd["api_equiv_usd"]) == ("2026-03", "Small", 93, 10)
    assert mtd["days_elapsed"] == 10 and mtd["projected_api_equiv_usd"] == pytest.approx(31)
    assert mtd["projected_value_multiple"] == pytest.approx(0.33, abs=0.005)
    assert mtd["plan_value_days_used"] == pytest.approx(3.3) and mtd["complete"]


def test_no_subscription_file_leaves_plan_fields_null(sub_projects, tmp_path):
    u = sub_usage(sub_projects, tmp_path)
    assert u["subscription"]["status"] == "missing" and "subscription.json" in u["subscription"]["note"]
    assert '"monthly_usd"' in u["subscription"]["note"], "the note says how to write the file"
    assert all(d["subscription_usd_share"] is None for d in u["daily"])
    assert all(r["subscription_usd_share"] is None for r in u["projects"] + u["sessions"])
    assert u["total"]["subscription_usd"] is None and u["total"]["value_multiple"] is None
    assert all(m["subscription_usd"] is None and m["value_multiple"] is None for m in u["months"])
    mtd = u["month_to_date"]
    assert mtd["subscription_usd"] is None and mtd["plan_value_days_used"] is None
    assert mtd["projected_api_equiv_usd"] == pytest.approx(31), "the API-equivalent projection needs no plan"


def test_invalid_or_late_schedule(sub_projects, tmp_path):
    bad = sub_usage(sub_projects, tmp_path, raw='[{"from": "March", "monthly_usd": "a lot"}]')
    assert bad["subscription"]["status"] == "invalid" and bad["total"]["subscription_usd"] is None
    late = sub_usage(sub_projects, tmp_path, [{"from": "2026-03", "plan": "Small", "monthly_usd": 93}])
    mo = {m["month"]: m for m in late["months"]}
    assert mo["2026-02"]["plan"] is None and mo["2026-02"]["subscription_usd_window"] is None
    p = {r["project"]: r for r in late["projects"]}
    assert p["proj-a"]["subscription_usd_share"] == pytest.approx(15), "only March's plan dollars are shared"
    t = late["total"]
    assert t["api_equiv_usd_planned"] == 10 and t["subscription_usd"] == pytest.approx(30)
    assert t["value_multiple"] == pytest.approx(0.33, abs=0.005), "months with no plan stay out of the multiple"


def test_subscription_file_setting(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_SUBSCRIPTION_FILE", raising=False)
    monkeypatch.delenv("GNOMONKIT_SUBSCRIPTION_FILE", raising=False)
    assert E.subscription_file(tmp_path) == tmp_path / "subscription.json"
    monkeypatch.setenv("GNOMONKIT_SUBSCRIPTION_FILE", str(tmp_path / "other.json"))
    assert E.subscription_file(tmp_path) == tmp_path / "other.json"
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_SUBSCRIPTION_FILE", str(tmp_path / "opt.json"))
    assert E.subscription_file(tmp_path) == tmp_path / "opt.json"


def test_usage_cli_shows_plan(sub_projects, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk"))
    f = tmp_path / "plans.json"
    f.write_text(json.dumps(PLANS), encoding="utf-8")
    assert E.main(["--no-cache", "--projects", str(sub_projects), "--usage", "--since", "400",
                   "--subscription-file", str(f)]) == 0
    out = capsys.readouterr().out
    assert "plan share" in out and "days of plan value used" in out


# ------------------------------------------------------------------ hostile input (core 0.28.0)
def test_budgets_that_are_not_numbers_fall_back(tmp_path, monkeypatch):
    """A budget of 1e999 used to raise OverflowError in int() and stop every collector run."""
    for raw in ("1e999", "-1e999", "nan", "inf", "0", "-5", "", "x" * 100, None, True):
        assert E.budget_value(raw) is None, raw
    assert E.budget_value("1e300") == E.SETTING_BUDGET_MAX              # finite but absurd: clamped
    assert E.budget_value("300_000") == 300_000 and E.budget_value(400000.0) == 400_000
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CONTEXT_BUDGET", raising=False)
    for raw in ("1e999", "nan", "-inf"):
        monkeypatch.setenv("GNOMONKIT_CONTEXT_BUDGET", raw)
        assert E.context_budget() == E.DEFAULT_CONTEXT_BUDGET
    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    local = repo / ".claude" / "settings.local.json"
    (repo / ".claude" / "settings.json").write_text(json.dumps({"env": {"GNOMONKIT_CONTEXT_BUDGET": "200000"}}))
    bad = ['{"env": {"GNOMONKIT_CONTEXT_BUDGET": 1e999}}', '{"env": {"GNOMONKIT_CONTEXT_BUDGET": NaN}}',
           '{"env": {"GNOMONKIT_CONTEXT_BUDGET": "Infinity"}}', '{"env": ["GNOMONKIT_CONTEXT_BUDGET"]}',
           '{"env": "x"}', '["not", "a", "dict"]', "[" * 100_000 + "]" * 100_000,
           json.dumps({"env": {"GNOMONKIT_CONTEXT_BUDGET": "300000"}, "pad": "x" * E.SETTINGS_MAX_BYTES})]
    for text in bad:
        local.write_text(text)
        assert E.project_budget(str(repo), 250_000) == (200_000, "settings.json"), text[:40]


def test_tagged_blocks_are_found_without_a_regex():
    t = ("a<task-notification><task-id>x1</task-id> <subagent_tokens>1,200</subagent_tokens></task-notification>"
         "b<task-notification><task-id>never closed")
    got = list(E._blocks(t, E.NOTIF_OPEN, E.NOTIF_CLOSE))
    assert len(got) == 1 and got[0][0].startswith(E.NOTIF_OPEN) and got[0][0].endswith(E.NOTIF_CLOSE)
    assert E._tag(got[0][1], "task-id") == "x1" and E._tag(got[0][1], "subagent_tokens") == "1,200"
    assert E._tag("<task-id>never closed", "task-id") == ""


def test_gates_ran_needs_python_then_the_script_in_one_segment():
    assert E.gates_ran("python scripts/leakcheck.py --all") == {"leakcheck"}
    assert E.gates_ran('"/opt/py/bin/python3.12" scripts/provenance_check.py --stdin diff') == {"provenance"}
    assert E.gates_ran("git diff --cached | python scripts/provenance_check.py --stdin diff && "
                       "py -3 scripts/leakcheck.py --all") == {"provenance", "leakcheck"}
    assert E.gates_ran("cat scripts/leakcheck.py; python other.py") == set()
    assert E.gates_ran("python other.py --name xleakcheck.py") == set()
    assert E.gates_ran("gitleaks git --staged") == {"gitleaks"}


def test_transcript_text_scans_are_linear():
    """Synthetic worst cases: each took seconds or more before 0.28.0 (a regex retried from every unclosed
    opening); now each is a few milliseconds. The bound is loose so a slow runner still passes."""
    n = 20_000
    cases = {
        "notification": (lambda s: list(E._blocks(s, E.NOTIF_OPEN, E.NOTIF_CLOSE)), E.NOTIF_OPEN * n),
        "tag": (lambda s: E._tag(s, "task-id"), "<task-id>" * n),
        "gates": (E.gates_ran, "py " * n),
        "gate script": (E.gates_ran, "python x_leakcheck.py " * n),
        "script name": (E._SCRIPT.findall, "a." * n),
        "skill line": (E._SKILL_LINE.findall, "\n" * n),
        "activity": (lambda s: E.classify_call("Bash", {"command": s}), "open(" * n),
        "quoted": (E._shell_segments, '"\\ ' * n),
    }
    for name, (f, s) in cases.items():
        t0 = time.perf_counter()
        f(s)
        assert time.perf_counter() - t0 < 1.5, name


def test_collector_files_are_written_privately(tmp_path):
    d = tmp_path / "fresh" / "effectiveness"
    target = d / "cache.json"
    E.write_private(target, "{}")
    assert target.read_text(encoding="utf-8") == "{}"
    assert [p.name for p in d.iterdir()] == ["cache.json"]               # no temp file left behind
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600 and d.stat().st_mode & 0o777 == 0o700
        victim = tmp_path / "victim"
        victim.write_text("keep", encoding="utf-8")
        os.symlink(victim, d / "cache.json.tmp")                         # where the old writer put its temp file
        E.write_private(target, '{"a": 1}')
        assert victim.read_text(encoding="utf-8") == "keep" and json.loads(target.read_text()) == {"a": 1}


# ---- spawn tracking: model, accuracy signals, dispatched sessions --------------------------------------

def on_model(turn, model):
    turn["message"]["model"] = model
    return turn


def sub_files(proj, sess, aid, agent_type, tid, model, reads):
    sub = proj / sess / "subagents"
    write(sub / ("agent-%s.jsonl" % aid), [
        prompt("go"),
        on_model(a_turn("s" + aid, *[tool_use(aid + str(i), "Read", file_path=p) for i, p in enumerate(reads)]), model),
        on_model(a_turn("t" + aid, text("found it")), model)])
    (sub / ("agent-%s.meta.json" % aid)).write_text(json.dumps(
        {"agentType": agent_type, "toolUseId": TU + tid, "spawnDepth": 1, "requestShape": "foreground"}))


@pytest.fixture
def spawn_home(tmp_path):
    h = tmp_path / "claude-sig"
    proj = h / "projects" / "sig-proj"
    write(proj / "sg.jsonl", [
        prompt("start"),
        # P: the parent re-reads what the subagent read, then the user corrects it: redone, corrected
        a_turn("m1", tool_use("P", "Agent", subagent_type="collector", description="p")),
        result("P", "agentId: aaa1\n" + "r" * 100),
        a_turn("m2", tool_use("P1", "Read", file_path="/src/one.txt")),
        result("P1", "x"),
        prompt("no, that is wrong, you missed the file"),
        a_turn("m3", text("sorry")),
        # Q: the parent reads other files, the user moves on, nobody calls it wrong: all three negative
        a_turn("m4", tool_use("Q", "Agent", subagent_type="checker", description="q")),
        result("Q", "agentId: bbb2\n" + "r" * 100),
        a_turn("m5", tool_use("Q1", "Read", file_path="/src/other.txt")),
        result("Q1", "x"),
        prompt("thanks, carry on"),
        a_turn("m6", text("the agent found it, moving on")),
        # R: the parent says the result was incomplete, with no further prompt or tool call after it
        a_turn("m7", tool_use("R", "Agent", subagent_type="Explore", description="r")),
        result("R", "agentId: ccc3\n" + "r" * 100),
        a_turn("m8", text("the agent's result was incomplete, I will look myself")),
    ])
    sub_files(proj, "sg", "aaa1", "collector", "P", "claude-haiku-9", ["/src/one.txt", "/src/two.txt"])
    sub_files(proj, "sg", "bbb2", "checker", "Q", "claude-sonnet-9", ["/src/three.txt"])
    sub_files(proj, "sg", "ccc3", "Explore", "R", "claude-haiku-9", ["/src/four.txt"])
    return h


def test_spawn_model_and_signals(spawn_home, gk, tmp_path):
    m = run(spawn_home, gk, tmp_path)
    rows = rows_by_agent(m)
    assert [rows[a]["model"] for a in ("aaa1", "bbb2", "ccc3")] == ["claude-haiku-9", "claude-sonnet-9", "claude-haiku-9"]
    assert rows["aaa1"]["effort"] is None
    pick = lambda a: {k: rows[a][k] for k in E.SIGNALS}          # noqa: E731
    assert pick("aaa1") == {"redone": True, "corrected": True, "rejected": False}
    assert pick("bbb2") == {"redone": False, "corrected": False, "rejected": False}
    # R: nothing follows the return except the text that rejects it, so redone / corrected can't be measured
    assert pick("ccc3") == {"redone": None, "corrected": None, "rejected": True}


def test_signal_rates_group_by_type_and_model(spawn_home, gk, tmp_path):
    s = run(spawn_home, gk, tmp_path)["subagents"]
    hk = s["by_model"]["claude-haiku-9"]
    assert hk["spawns"] == 2
    assert hk["signals"]["redone"] == {"hits": 1, "n": 1, "rate": 1.0, "thin": True}
    assert hk["signals"]["rejected"] == {"hits": 1, "n": 2, "rate": 0.5, "thin": True}
    assert s["by_type"]["checker"]["signals"]["corrected"]["rate"] == 0.0
    nothing = s["by_type"]["Explore"]["signals"]["corrected"]
    assert nothing == {"hits": 0, "n": 0, "rate": None, "thin": False}      # n/a, not zero
    ch = s["cheap_types"]
    assert ch["spawns"] == 2 and ch["share_of_spawns"] == round(2 / 3, 3)
    assert s["labels"]["redone"] == "SIGNAL"


def test_signal_patterns_are_calibrated():
    yes = ["the agent's result was incomplete", "the subagent got it wrong", "that answer is incorrect",
           "its report made it up", "the agent missed the second file"]
    no = ["the agent found the bug", "the result is complete", "wrong branch checked out, switching",
          "I missed it earlier", "the agent finished. Incomplete tests are listed below."]
    assert all(E._REJECT.search(t) for t in yes)
    assert not any(E._REJECT.search(t) for t in no)


def test_summary_table_shows_signals_and_cheap_types(spawn_home, gk, tmp_path):
    t = E.table(run(spawn_home, gk, tmp_path))
    assert "SPAWN SIGNALS" in t and "model:claude-haiku-9" in t and "type:checker" in t
    assert "cheap types (collector, checker): 2 spawns" in t
    assert "1/1~" in t


def test_no_cheap_types_says_so(home, gk, tmp_path):
    m = run(home, gk, tmp_path)
    assert m["subagents"]["cheap_types"]["spawns"] == 0
    assert "none used in the window" in E.table(m)


def dispatch_record(sid, **extra):
    rec = {"role": "sub", "project": "/work/demo-app", "task": "t", "name": "sub:main/x", "parent": "main",
           "started": TS[:19] + ".000Z", "pr": None, "done": None, "merged": None, "sessionId": sid}
    rec.update(extra)
    return rec


def test_dispatched_sessions_read_roles_as_data(tmp_path, gk):
    h = tmp_path / "claude-d"
    proj = h / "projects" / "d-proj"
    write(proj / "sid1.jsonl", [
        prompt("Read your brief at the path named here"),             # the fixed first prompt: not counted
        on_model(a_turn("d1", text("ok")), "claude-sonnet-9"),
        prompt("thanks"), prompt("you didn't run the tests"), prompt("fine")])
    d = gk / "dispatch"
    d.mkdir()
    (d / "roles.json").write_text(json.dumps({
        "k1": dispatch_record("sid1", pr="https://example.invalid/pr/1", merged="2026-10-29T10:00:00Z"),
        "k2": dispatch_record("missing", model="haiku", effort="low", role="primary", name=None),
        "old": dispatch_record("sid1", started="2020-01-01T00:00:00.000Z"),
    }))
    dp = run(h, gk, tmp_path)["dispatched"]
    assert dp["sessions"] == 2 and dp["not_recorded"] == ["ci_before_merge"]
    r = {x["key"]: x for x in dp["rows"]}
    assert r["k1"]["model"] == "claude-sonnet-9" and r["k1"]["merged"] and r["k1"]["transcript_found"]
    assert (r["k1"]["prompts"], r["k1"]["corrections"]) == (3, 1)
    assert r["k2"]["model"] == "haiku" and r["k2"]["effort"] == "low" and not r["k2"]["transcript_found"]
    assert dp["total"]["corrections_per_prompt"] == {"hits": 1, "n": 3, "rate": 0.333, "thin": True}
    assert dp["total"]["pr_merged"] == {"hits": 1, "n": 1, "rate": 1.0, "thin": True}
    assert set(dp["by_role"]) == {"sub", "primary"}
    assert "DISPATCHED SESSIONS  2" in E.table(run(h, gk, tmp_path))


def test_dispatched_missing_or_bad_roles_file(home, gk, tmp_path):
    assert run(home, gk, tmp_path)["dispatched"]["sessions"] == 0
    (gk / "dispatch").mkdir()
    (gk / "dispatch" / "roles.json").write_text("not json")
    assert run(home, gk, tmp_path)["dispatched"]["sessions"] == 0
