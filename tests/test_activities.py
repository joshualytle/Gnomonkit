"""Where the cost goes: the activity taxonomy, per-turn attribution and splitting, subagent roll-up, context
bands, processes joined with automate-repeats, the CLI table, and the portal's whitelist. Synthetic data only."""
import datetime
import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("effectiveness_act", ROOT / "plugins" / "core" / "skills" /
                                              "effectiveness" / "effectiveness.py")
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)
sys.path.insert(0, str(ROOT / "plugins" / "portal"))
from gnomonkit_portal import view  # noqa: E402

NOW = time.time()
TS = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - 3600))
TU = "tool" + "u_"
MODEL = "claude-haiku-4-5"        # $1 input, $5 output, $1.25 cache write, $0.10 cache read per million


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GNOMONKIT_HOME", str(tmp_path / "gk-default"))
    monkeypatch.setenv("GNOMONKIT_BENCH_DIR", str(tmp_path / "bench-default"))
    for k in ("CLAUDE_PLUGIN_OPTION_SUBSCRIPTION_FILE", "GNOMONKIT_SUBSCRIPTION_FILE"):
        monkeypatch.delenv(k, raising=False)


def turn(mid, *blocks, inp=0, cw=0, cr=0, out=0, ts=TS):
    return {"type": "assistant", "timestamp": ts, "cwd": "/work/act-app",
            "message": {"id": mid, "model": MODEL, "role": "assistant",
                        "content": list(blocks) or [{"type": "text", "text": "thinking aloud"}],
                        "usage": {"input_tokens": inp, "cache_creation_input_tokens": cw,
                                  "cache_read_input_tokens": cr, "output_tokens": out}}}


def call(tid, name, **inp):
    return {"type": "tool_use", "id": TU + tid, "name": name, "input": inp}


def result(tid, body="ok"):
    return {"type": "user", "timestamp": TS,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": TU + tid, "content": body}]}}


def prompt(t):
    return {"type": "user", "timestamp": TS, "message": {"role": "user", "content": t}}


def write(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    # compact, as Claude Code writes them: the scanner's cheap pre-filter looks for '"type":"assistant"'
    path.write_text("".join(json.dumps(x, separators=(",", ":")) + "\n" for x in lines), encoding="utf-8")


def collect(projects, tmp_path, **kw):
    return E.collect(30, gk=tmp_path / "gk", cache_path=None, now=NOW, projects=projects, **kw)


# ---------------------------------------------------------------------------
# taxonomy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,inp,want", [
    ("Bash", {"command": "cd /w && python -m pytest -q && git commit -m 'run pytest' && git push"}, "shipping"),
    ("Bash", {"command": "gh pr create --title t --body-file b.md"}, "shipping"),
    ("PowerShell", {"command": "gh pr merge 12 --squash"}, "shipping"),
    ("Bash", {"command": "git -C /w push origin feat"}, "shipping"),
    ("Bash", {"command": "flutter test test/a_test.dart"}, "tests and builds"),
    ("Bash", {"command": "npm run build:web"}, "tests and builds"),
    ("Bash", {"command": "cargo test --all"}, "tests and builds"),
    ("PowerShell", {"command": "dotnet build App.sln"}, "tests and builds"),
    ("Bash", {"command": "make -j4"}, "tests and builds"),
    ("Bash", {"command": "gh pr checks 12 --watch"}, "CI and waiting"),
    ("Bash", {"command": "gh run watch 99"}, "CI and waiting"),
    ("Bash", {"command": "while true; do sleep 30; done"}, "CI and waiting"),
    ("Monitor", {}, "CI and waiting"),
    ("Bash", {"command": "sed -i 's/a/b/' x.py"}, "editing"),
    ("Bash", {"command": "python - <<'EOF'\nopen('x.py', 'w').write(s)\nEOF"}, "editing"),
    ("Edit", {"file_path": "/w/a.py"}, "editing"),
    ("Bash", {"command": "sed -n 1,40p x.py"}, "reading code"),
    ("Bash", {"command": "rg -n needle src | head"}, "reading code"),
    ("Grep", {"pattern": "x"}, "reading code"),
    ("mcp__palace__search", {}, "search and memory"),
    ("WebFetch", {}, "search and memory"),
    ("Agent", {"subagent_type": "Explore"}, "orchestration"),
    ("SendMessage", {}, "orchestration"),
    ("AskUserQuestion", {}, "user-facing"),
    ("Artifact", {}, "user-facing"),
    ("mcp__claude_ai_Example_Docs__update", {}, "user-facing"),
    ("mcp__browser__click", {}, "mcp: browser"),
    ("NewShinyTool", {}, "other: NewShinyTool"),
])
def test_taxonomy(name, inp, want):
    assert E.classify_call(name, inp)[0] == want


def test_shell_other_keeps_the_program():
    assert E.classify_call("Bash", {"command": "cd /w && uptime"}) == ("shell other", "uptime")
    assert E.classify_call("PowerShell", {"command": "& \"C:/tools/bin/widget.exe\" --go"}) == ("shell other", "widget")
    assert E.classify_call("Bash", {"command": "X=1 timeout 60 curl -s u"}) == ("shell other", "curl")


def test_rules_are_one_ordered_table():
    names = [r["activity"] for r in E.ACTIVITY_RULES]
    assert names[0] == "shipping" and len(names) == len(set(names))


# ---------------------------------------------------------------------------
# attribution
# ---------------------------------------------------------------------------

@pytest.fixture
def act_projects(tmp_path):
    root = tmp_path / "projects"
    proj = root / "act-proj"
    write(proj / "a1.jsonl", [
        prompt("go"),
        # $1 work + $1 carry, split evenly between a read and an edit
        turn("m1", call("r1", "Read", file_path="/w/a.py"), call("e1", "Edit", file_path="/w/a.py"),
             inp=1_000_000, cr=10_000_000),
        result("r1"), result("e1"),
        turn("m2", call("b1", "Bash", command="cd /w && python -m pytest -q && git commit -m 'x' && git push"),
             out=200_000),                                                     # $1 work, shipping
        result("b1"),
        turn("m3", cw=800_000),                                                # $1 carry, no tool call
        turn("m3", cw=800_000),                                                # the same message again: once
        turn("m4", call("b2", "Bash", command="uptime"), inp=1_000_000),       # $1 work, shell other
        result("b2"),
        turn("m5", call("A", "Agent", subagent_type="Explore", description="look"), out=200_000),   # $1
        result("A", "agentId: sub1\nfound"),
    ])
    sub = proj / "a1" / "subagents"
    write(sub / "agent-sub1.jsonl", [
        prompt("explore"),
        turn("s1", call("g1", "Grep", pattern="x"), cr=20_000_000),            # $2 carry
        result("g1"),
        turn("s2", inp=1_000_000),                                             # $1 work, reply
    ])
    (sub / "agent-sub1.meta.json").write_text(json.dumps({"agentType": "Explore", "toolUseId": TU + "A"}))
    return root


def by_act(a):
    return {r["activity"]: r for r in a["activities"]}


def test_attribution_split_and_parts(act_projects, tmp_path):
    m = collect(act_projects, tmp_path)
    a = m["activities"]
    assert a["total"]["cost_usd"] == pytest.approx(9.0) == pytest.approx(m["usage"]["total"]["est_cost_usd"])
    assert a["total"]["turns"] == m["usage"]["total"]["turns"] == 7
    assert a["total"]["split_turns"] == 1
    r = by_act(a)
    assert r["reading code"]["cost_usd"] == pytest.approx(1.0) and r["editing"]["cost_usd"] == pytest.approx(1.0)
    assert r["reading code"]["context_carry_usd"] == pytest.approx(0.5) and r["reading code"]["work_usd"] == pytest.approx(0.5)
    assert r["reading code"]["context_carry_share"] == pytest.approx(0.5)
    assert r["shipping"]["cost_usd"] == pytest.approx(1.0) and r["shipping"]["context_carry_share"] == 0
    assert r["reasoning/reply"]["cost_usd"] == pytest.approx(1.0) and r["reasoning/reply"]["context_carry_share"] == 1
    assert r["shell other"]["programs"][0]["program"] == "uptime"
    assert r["orchestration"]["cost_usd"] == pytest.approx(1.0)
    assert r["reading code"]["turns"] == 1 and r["reading code"]["sessions"] == 1
    assert a["total"]["context_carry_usd"] == pytest.approx(4.0) and a["total"]["work_usd"] == pytest.approx(5.0)
    assert sum(x["share"] for x in a["activities"]) == pytest.approx(1.0, abs=1e-3)
    assert "_alloc" not in m["usage"]


def test_subagent_rolls_up_with_inner_breakdown(act_projects, tmp_path):
    r = by_act(collect(act_projects, tmp_path)["activities"])
    s = r["subagent: Explore"]
    assert s["cost_usd"] == pytest.approx(3.0) and s["turns"] == 2 and s["group"] == "subagent"
    inner = {x["activity"]: x for x in s["inner"]}
    assert inner["reading code"]["cost_usd"] == pytest.approx(2.0) and inner["reading code"]["share"] == pytest.approx(2 / 3, abs=1e-3)
    assert inner["reasoning/reply"]["cost_usd"] == pytest.approx(1.0)
    assert r["reading code"]["cost_usd"] == pytest.approx(1.0), "a subagent's reads stay inside its row"


def test_subscription_share_matches_usage_allocation(act_projects, tmp_path):
    f = tmp_path / "plan.json"
    f.write_text(json.dumps([{"from": "2020-01", "plan": "Test plan", "monthly_usd": 90}]), encoding="utf-8")
    m = collect(act_projects, tmp_path, subscription_path=f)
    acts = m["activities"]["activities"]
    assert all(x["subscription_usd_share"] is not None for x in acts)
    assert sum(x["subscription_usd_share"] for x in acts) == \
        pytest.approx(m["usage"]["total"]["subscription_usd_allocated"], abs=1e-3)
    r = by_act(m["activities"])
    assert r["subagent: Explore"]["subscription_usd_share"] == pytest.approx(
        m["usage"]["total"]["subscription_usd_allocated"] / 3, abs=1e-3)


def test_no_plan_no_share(act_projects, tmp_path):
    acts = collect(act_projects, tmp_path)["activities"]["activities"]
    assert all(x["subscription_usd_share"] is None for x in acts)


def test_context_bands(tmp_path):
    root = tmp_path / "projects"
    write(root / "band-proj" / "b1.jsonl", [prompt("go")] + [
        turn("k%d" % k, call("t%d" % k, "Read", file_path="/w/f"), inp=k * 1000) for k in (100, 300, 500, 700)])
    bands = {b["above"]: b for b in collect(root, tmp_path)["activities"]["context_bands"]}
    assert sorted(bands) == [200_000, 400_000, 600_000]
    assert bands[200_000]["cost_share"] == pytest.approx(1.5 / 1.6, abs=1e-3)
    assert bands[400_000]["cost_share"] == pytest.approx(1.2 / 1.6, abs=1e-3)
    assert bands[600_000]["cost_share"] == pytest.approx(0.7 / 1.6, abs=1e-3)
    assert (bands[200_000]["turns_share"], bands[600_000]["turns_share"]) == (0.75, 0.25)


def test_processes_get_cost_per_run(tmp_path):
    root = tmp_path / "projects"
    for i in range(3):
        write(root / "ship-proj" / ("p%d.jsonl" % i), [
            prompt("ship it"),
            turn("p%da" % i, call("p%da" % i, "Bash", command="git add -A"), out=200_000),
            turn("p%db" % i, call("p%db" % i, "Bash", command='git commit -m "msg"'), out=200_000),
            turn("p%dc" % i, call("p%dc" % i, "Bash", command="git push"), out=200_000),
        ])
    a = collect(root, tmp_path)["activities"]
    top = a["processes"][0]
    assert top["shape"] == ["git add", "git commit", "git push"]
    assert (top["runs"], top["sessions"]) == (3, 3)
    assert top["cost_usd"] == pytest.approx(9.0) and top["cost_usd_per_run"] == pytest.approx(3.0)
    assert top["share"] == pytest.approx(1.0)


def test_cli_prints_activities(act_projects, tmp_path, capsys):
    assert E.main(["--no-cache", "--projects", str(act_projects), "--activities"]) == 0
    out = capsys.readouterr().out
    assert "Where it goes" in out and "subagent: Explore" in out and "context above" in out
    assert "reading code" in out and "programs: uptime" in out


# ---------------------------------------------------------------------------
# portal whitelist
# ---------------------------------------------------------------------------

XSS = "<script>alert(1)</script>"
HOST = "build" + "box-" + "internal"
SCRIPT = "deploy_" + "secret" + ".py"
PATH = "/home/" + "someone" + "/bin/tool"


def act_metrics():
    return {"activities": {
        "label": "API-equivalent estimate", "method": "how",
        "total": {"cost_usd": 10.0, "context_carry_share": 0.9, "turns": 5},
        "activities": [
            {"activity": "reading code", "group": "core", "cost_usd": 5.0, "share": 0.5, "context_carry_usd": 4.5,
             "work_usd": 0.5, "turns": 3, "sessions": 1},
            {"activity": "shell other", "group": "core", "cost_usd": 2.0, "programs": [
                {"program": "curl", "cost_usd": 1.0, "turns": 2}, {"program": PATH, "cost_usd": 1.0, "turns": 1},
                {"program": XSS, "cost_usd": 0.1, "turns": 1}]},
            {"activity": "subagent: " + XSS, "group": "subagent", "cost_usd": 1.0,
             "inner": [{"activity": "mcp: " + PATH, "cost_usd": 1.0}]},
            {"activity": XSS, "group": "evil", "cost_usd": 0.5, "command": "ssh " + HOST},
            {"activity": "mcp: browser", "group": "mcp", "cost_usd": 0.4},
        ],
        "processes": [{"pattern": "ssh %s -> python %s" % (HOST, SCRIPT), "runs": 3, "cost_usd": 3.0,
                       "cost_usd_per_run": 1.0,
                       "shape": ["git push origin", "python " + SCRIPT, "ssh " + HOST, "gh pr create",
                                 "python -m pytest", "mcp__" + "srv" + "__do_thing", "npm " + HOST]}],
        "process_rule": {"min_sessions": 3},
        "context_bands": [{"above": 200000, "cost_share": 0.8, "turns_share": 0.6}],
    }}


def test_view_whitelists_activity_names_programs_and_steps():
    a = view.public_view(act_metrics())["sections"]["activities"]
    names = [r["activity"] for r in a["activities"]]
    assert names == ["reading code", "shell other", "subagent: unnamed", "unknown", "mcp: browser"]
    assert a["activities"][1]["programs"] == [{"cost_usd": 1.0, "turns": 2, "program": "curl"}]
    assert a["activities"][2]["inner"][0]["activity"] == "mcp: unnamed"
    assert a["activities"][3]["group"] == "core" and "command" not in a["activities"][3]
    p = a["processes"][0]
    assert "pattern" not in p and "shape" not in p
    assert p["steps"] == ["git push", "python", "ssh", "gh pr create", "python -m pytest", "mcp: srv", "npm"]
    assert p["cost_usd_per_run"] == 1.0 and a["context_bands"][0]["cost_share"] == 0.8
    blob = view.dumps(view.public_view(act_metrics())).decode("utf-8")
    for bad in (HOST, SCRIPT, PATH, "<script"):
        assert bad not in blob


def test_view_without_activities_has_no_section():
    assert "activities" not in view.public_view({"activities": {"no": "rows"}})["sections"]
    assert "activities" not in view.public_view({})["sections"]


def test_page_draws_the_where_it_goes_tab():
    page = (ROOT / "plugins" / "portal" / "gnomonkit_portal" / "page.html").read_text(encoding="utf-8")
    assert "Where it goes" in page and "renderActivities" in page and '"activities"' in page
    for field in ("context_carry_usd", "work_usd", "cost_usd_per_run", "context_bands", "steps"):
        assert field in page
    for theme in (':root{color-scheme:light;', ':root:not([data-theme="light"])', ':root[data-theme="dark"]'):
        block = page.split(theme, 1)[1].split("}", 1)[0]
        assert "--bd3:" in block, "band colours are defined for every theme"
