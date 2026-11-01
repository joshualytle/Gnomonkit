"""Round runner: records, fail-fast, spending guard, scrubbing, and the host/container refusals.
A fake launcher writes synthetic streams; nothing here starts Claude."""
import json
from pathlib import Path

import pytest
from streams import init, result, tool_use, write_stream

from bench.arms import build_arms
from bench.runner import BenchAbort, estimate, plan_round, run_round, scrub
from bench.scenario import Scenario

CORE = Path(__file__).resolve().parents[2] / "plugins" / "core"
SC = Scenario(name="t01", family="skills", polarity="positive", files={"app.py": "x = 1\n"},
              prompt="Do it. Do the work directly, without asking questions.",
              score=lambda repo, t: {"done": True, "applied": (repo / "app.py").exists()})


@pytest.fixture
def arms(tmp_path):
    return build_arms(CORE, tmp_path / "work", custom=(tmp_path / "plug",))


def jobs_of(n, arm="gnomonkit"):
    return [(SC, arm, i) for i in range(n)]


def fake(cost=0.1, rc=0, with_result=True, calls=None):
    def launcher(cmd, cwd, env, timeout, stream_path):
        if calls is not None:
            calls.append((cmd, env))
        events = [init(), tool_use("Write", {"file_path": str(Path(cwd) / "app.py")})]
        if with_result:
            events.append(result(cost=cost))
        write_stream(Path(stream_path).parent, events, name=Path(stream_path).name)
        Path(stream_path).with_suffix(".err").write_text(f"error near {cwd}\n", encoding="utf-8")
        return rc
    return launcher


def raising(exc):
    def launcher(*a, **k):
        raise exc("claude")
    return launcher


def test_records_and_results_file(tmp_path, arms):
    recs = run_round(jobs_of(1), arms, tmp_path / "out", "sonnet", launcher=fake(cost=0.1), allow_host=True)
    assert len(recs) == 1 and recs[0]["billing"] in ("api", "subscription")
    r = recs[0]
    assert (r["scenario"], r["arm"], r["applied"], r["cost_usd"], r["version"]) == ("t01", "gnomonkit", True, 0.1, "2.1.303")
    data = json.loads((tmp_path / "out" / "results.json").read_text(encoding="utf-8"))
    assert data["schema"] == "gnomonkit-bench/v1" and data["model"] == "sonnet" and len(data["records"]) == 1


def test_launcher_gets_allowlisted_env_only(tmp_path, arms, monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "synthetic-not-real")
    calls = []
    run_round(jobs_of(1), arms, tmp_path / "out", "sonnet", launcher=fake(calls=calls), allow_host=True)
    env = calls[0][1]
    assert "CLOUDFLARE_API_TOKEN" not in env and env["GNOMONKIT_MEMORY_GLOB"] == "" and "CLAUDE_CONFIG_DIR" in env


def test_each_run_gets_its_own_config_home_tmp_and_gitconfig(tmp_path, arms):
    """Review #1/#2: a shared CLAUDE_CONFIG_DIR let one run plant CLAUDE.md for every later run."""
    calls = []
    run_round(jobs_of(2), arms, tmp_path / "out", "sonnet", launcher=fake(calls=calls), workers=1, allow_host=True)
    (e1, e2) = (calls[0][1], calls[1][1])
    for k in ("CLAUDE_CONFIG_DIR", "HOME", "TEMP", "GIT_CONFIG_GLOBAL"):
        assert e1[k] != e2[k], k
        assert Path(e1[k]).resolve().is_relative_to((tmp_path / "out").resolve()), k


def test_only_the_login_file_is_copied_in(tmp_path, arms):
    auth = tmp_path / "auth"
    auth.mkdir()
    (auth / ".credentials.json").write_text('{"synthetic": true}', encoding="utf-8")
    (auth / "CLAUDE.md").write_text("planted instructions", encoding="utf-8")
    seen = {}
    inner = fake()

    def launcher(cmd, cwd, env, timeout, stream_path):        # look while the run is live
        cfg = Path(env["CLAUDE_CONFIG_DIR"])
        seen["login"] = (cfg / ".credentials.json").exists()
        seen["claude_md"] = (cfg / "CLAUDE.md").exists()
        return inner(cmd, cwd, env, timeout, stream_path)

    run_round(jobs_of(1), arms, tmp_path / "out", "sonnet", launcher=launcher, allow_host=True,
              billing="subscription", auth_dir=auth)
    assert seen == {"login": True, "claude_md": False}


def test_login_copy_is_deleted_after_each_run(tmp_path, arms):
    """A round must not leave copies of the user's login on disk, one per run."""
    auth = tmp_path / "auth"
    auth.mkdir()
    (auth / ".credentials.json").write_text('{"synthetic": true}', encoding="utf-8")
    run_round(jobs_of(2), arms, tmp_path / "out", "sonnet", launcher=fake(), workers=1, allow_host=True,
              billing="subscription", auth_dir=auth)
    assert list((tmp_path / "out").rglob(".credentials.json")) == []
    assert (auth / ".credentials.json").exists()


def test_custom_arm_refuses_subscription_login(tmp_path, arms):
    """Third-party plugin code must never see the subscription login; it needs a dedicated API key."""
    with pytest.raises(BenchAbort):
        run_round(jobs_of(1, arm="custom"), arms, tmp_path / "out", "sonnet", launcher=fake(), container=True,
                  billing="subscription")


def test_max_cost_stops_scheduling(tmp_path, arms):
    recs = run_round(jobs_of(10), arms, tmp_path / "out", "sonnet", launcher=fake(cost=1.0), workers=1,
                     max_cost=3.0, allow_host=True)
    assert len(recs) == 3


def test_missing_cli_aborts_round(tmp_path, arms):
    with pytest.raises(BenchAbort):
        run_round(jobs_of(5), arms, tmp_path / "out", "sonnet", launcher=raising(FileNotFoundError), allow_host=True)


def test_first_run_failing_without_result_aborts(tmp_path, arms):
    with pytest.raises(BenchAbort):
        run_round(jobs_of(5), arms, tmp_path / "out", "sonnet", launcher=fake(rc=1, with_result=False),
                  workers=1, allow_host=True)


def test_first_run_timeout_does_not_abort_and_counts_as_behaviour(tmp_path, arms):
    """Review #4/#5: a legitimate timeout is the agent's behaviour, not a broken launch."""
    recs = run_round(jobs_of(3), arms, tmp_path / "out", "sonnet", launcher=fake(rc=-9, with_result=False),
                     workers=1, allow_host=True)
    assert len(recs) == 3 and all(r["error_kind"] == "behaviour" for r in recs)


def login_failure():
    def launcher(cmd, cwd, env, timeout, stream_path):
        write_stream(Path(stream_path).parent, [init(), result(r="Invalid API key · Please run /login", cost=0,
                                                               is_error=True)], name=Path(stream_path).name)
        Path(stream_path).with_suffix(".err").write_text("", encoding="utf-8")
        return 1
    return launcher


def test_first_run_login_error_with_a_result_aborts(tmp_path, arms):
    with pytest.raises(BenchAbort):
        run_round(jobs_of(5), arms, tmp_path / "out", "sonnet", launcher=login_failure(), workers=1, allow_host=True)


def test_error_tail_is_scrubbed(tmp_path, arms):
    recs = run_round(jobs_of(1), arms, tmp_path / "out", "sonnet", launcher=fake(rc=3), allow_host=True)
    assert recs[0]["rc"] == 3 and "<run>" in recs[0]["stderr"] and str(tmp_path) not in recs[0]["stderr"]


def test_scrub_hides_run_and_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    s = scrub(f"boom at {tmp_path / 'run' / 'x.py'} and {tmp_path / 'home' / '.ssh'}", tmp_path / "run")
    assert str(tmp_path) not in s and "<run>" in s and "<home>" in s


def test_host_run_needs_allow_host_and_custom_needs_container(tmp_path, arms):
    with pytest.raises(BenchAbort):
        run_round(jobs_of(1), arms, tmp_path / "out", "sonnet", launcher=fake(), allow_host=False)
    with pytest.raises(BenchAbort):
        run_round(jobs_of(1, arm="custom"), arms, tmp_path / "out", "sonnet", launcher=fake(), allow_host=True,
                  container=False)


def test_out_dir_inside_repo_refused(tmp_path, arms):
    with pytest.raises(BenchAbort):
        run_round(jobs_of(1), arms, Path(__file__).resolve().parent / "out", "sonnet", launcher=fake(),
                  allow_host=True)


def test_plan_round_respects_scenario_arms(arms):
    only_bare = Scenario(name="t02", family="skills", polarity="positive", files={}, prompt="p",
                         score=SC.score, arms=("bare", "written"))
    jobs = plan_round([SC, only_bare], arms, ("bare", "gnomonkit"), reps=2)
    assert {(s.name, a) for s, a, _ in jobs} == {("t01", "bare"), ("t01", "gnomonkit"), ("t02", "bare")}
    assert len(jobs) == 6


def _alive(pid):
    import subprocess as sp
    import sys
    if sys.platform == "win32":
        out = sp.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    import os
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_timeout_kills_the_whole_tree(tmp_path):
    """A timed-out run must not leave its children running: an orphaned agent keeps burning CPU for hours."""
    import sys
    import time
    from bench.runner import subprocess_launcher
    pidfile = tmp_path / "grandchild.pid"
    code = ("import subprocess,sys,time;"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
            f"open(r'{pidfile}','w').write(str(p.pid));time.sleep(120)")
    # 10 s: on a loaded machine a few seconds is not always enough for the child to write the pid file.
    rc = subprocess_launcher([sys.executable, "-c", code], tmp_path, None, 10, tmp_path / "s.jsonl")
    assert rc == -9
    gpid = int(pidfile.read_text())
    for _ in range(20):
        if not _alive(gpid):
            break
        time.sleep(0.25)
    assert not _alive(gpid)


def test_grandchild_outliving_a_normal_exit_is_killed(tmp_path):
    """Review #7: a process the model detached kept running after its run ended normally."""
    import sys
    import time
    from bench.runner import subprocess_launcher
    pidfile = tmp_path / "orphan.pid"
    code = ("import subprocess,sys;"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']);"
            f"open(r'{pidfile}','w').write(str(p.pid))")
    rc = subprocess_launcher([sys.executable, "-c", code], tmp_path, None, 30, tmp_path / "s.jsonl")
    assert rc == 0
    gpid = int(pidfile.read_text())
    for _ in range(20):
        if not _alive(gpid):
            break
        time.sleep(0.25)
    assert not _alive(gpid)


def test_kill_all_stops_live_runs(tmp_path):
    """Review #7: on Ctrl+C the round kills every live run instead of waiting out their timeouts."""
    import sys
    import threading
    import time
    from bench import runner as R
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("rc", R.subprocess_launcher(
        [sys.executable, "-c", "import time;time.sleep(120)"], tmp_path, None, 120, tmp_path / "s.jsonl")))
    t.start()
    for _ in range(40):
        if R.LIVE:
            break
        time.sleep(0.1)
    assert R.LIVE
    R.kill_all()
    t.join(timeout=15)
    assert not t.is_alive() and result["rc"] != 0 and not R.LIVE


def test_failed_round_kills_runs_still_going(tmp_path, arms):
    """Review #7: when the round stops, live runs end now, not after their own timeouts."""
    import sys
    import time
    from bench.runner import subprocess_launcher
    calls = {"n": 0}

    def mixed(cmd, cwd, env, timeout, stream_path):
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(1.5)                      # let the second run start first
            return login_failure()(cmd, cwd, env, timeout, stream_path)
        return subprocess_launcher([sys.executable, "-c", "import time;time.sleep(120)"], cwd, None, 120,
                                   stream_path)

    t0 = time.time()
    with pytest.raises(BenchAbort):
        run_round(jobs_of(2), arms, tmp_path / "out", "sonnet", launcher=mixed, workers=2, allow_host=True)
    assert time.time() - t0 < 30


def test_estimate_uses_last_round_median(tmp_path):
    prev = tmp_path / "prev"
    prev.mkdir()
    recs = [{"cost_usd": c} for c in (0.1, 0.2, 0.9)]
    (prev / "results.json").write_text(json.dumps({"schema": "gnomonkit-bench/v1", "records": recs}), encoding="utf-8")
    assert estimate(10, prev) == pytest.approx(2.0)
    assert estimate(10, None) == pytest.approx(2.5)
